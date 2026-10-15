package com.kmsp.tracker.ble

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Build
import android.os.ParcelUuid
import android.util.Log
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

private const val TAG = "KMSP_BLE"

val NUS_SERVICE_UUID: UUID = UUID.fromString("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
val NUS_RX_UUID: UUID = UUID.fromString("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
val NUS_TX_UUID: UUID = UUID.fromString("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

class BleNusClient(private val context: Context) {

    private val bluetoothAdapter: BluetoothAdapter? by lazy {
        val manager = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
        manager.adapter
    }

    @SuppressLint("MissingPermission")
    fun transmitBatchPayload(payloadJson: String, timeoutSec: Long = 10): Result<Int> {
        val adapter = bluetoothAdapter
        if (adapter == null || !adapter.isEnabled) {
            return Result.failure(IllegalStateException("Bluetooth is disabled or unavailable"))
        }

        val scanner = adapter.bluetoothLeScanner
            ?: return Result.failure(IllegalStateException("BLE Scanner unavailable"))

        Log.i(TAG, "Scanning for KMSP-Runway-Office peripheral...")
        var targetDevice: BluetoothDevice? = null
        val scanLatch = CountDownLatch(1)

        val scanCallback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult?) {
                val device = result?.device ?: return
                val name = result.scanRecord?.deviceName ?: device.name
                if (name != null && name.contains("KMSP-Runway", ignoreCase = true)) {
                    Log.i(TAG, "Found target peripheral: $name (${device.address}, RSSI: ${result.rssi})")
                    targetDevice = device
                    scanLatch.countDown()
                }
            }

            override fun onScanFailed(errorCode: Int) {
                Log.w(TAG, "BLE scan failed with error code $errorCode")
                scanLatch.countDown()
            }
        }

        val filter = ScanFilter.Builder()
            .setServiceUuid(ParcelUuid(NUS_SERVICE_UUID))
            .build()
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()

        // 1. Scan with 4-second discovery timeout
        try {
            scanner.startScan(listOf(filter), settings, scanCallback)
            scanLatch.await(4, TimeUnit.SECONDS)
        } finally {
            // ALWAYS explicitly stop scanning before connecting to eliminate GATT_ERROR 133
            scanner.stopScan(scanCallback)
        }

        val device = targetDevice ?: return Result.failure(IllegalStateException("Device KMSP-Runway-Office not found"))

        // 2. Connect GATT directly (autoConnect = false, TRANSPORT_LE)
        val connectLatch = CountDownLatch(1)
        val mtuLatch = CountDownLatch(1)
        val writeAckLatch = CountDownLatch(1)
        val writeSuccess = AtomicBoolean(false)
        var negotiatedMtu = 23
        var activeGatt: BluetoothGatt? = null
        var rxCharacteristic: BluetoothGattCharacteristic? = null

        val gattCallback = object : BluetoothGattCallback() {
            override fun onConnectionStateChange(gatt: BluetoothGatt?, status: Int, newState: Int) {
                Log.d(TAG, "GATT state change: status=$status, newState=$newState")
                if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                    Log.i(TAG, "Connected to GATT server. Requesting CONNECTION_PRIORITY_HIGH...")
                    gatt?.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
                    gatt?.requestMtu(512)
                    activeGatt = gatt
                    connectLatch.countDown()
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                    Log.i(TAG, "GATT disconnected cleanly.")
                    gatt?.close()
                    connectLatch.countDown()
                    mtuLatch.countDown()
                    writeAckLatch.countDown()
                }
            }

            override fun onMtuChanged(gatt: BluetoothGatt?, mtu: Int, status: Int) {
                Log.i(TAG, "MTU negotiated: $mtu (status: $status)")
                negotiatedMtu = if (status == BluetoothGatt.GATT_SUCCESS) mtu else 23
                gatt?.discoverServices()
            }

            override fun onServicesDiscovered(gatt: BluetoothGatt?, status: Int) {
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    val service = gatt?.getService(NUS_SERVICE_UUID)
                    rxCharacteristic = service?.getCharacteristic(NUS_RX_UUID)
                    Log.i(TAG, "Services discovered. RX characteristic ready: ${rxCharacteristic != null}")
                }
                mtuLatch.countDown()
            }

            override fun onCharacteristicWrite(gatt: BluetoothGatt?, characteristic: BluetoothGattCharacteristic?, status: Int) {
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    writeSuccess.set(true)
                } else {
                    Log.w(TAG, "Write failed with status $status")
                    writeSuccess.set(false)
                }
                writeAckLatch.countDown()
            }
        }

        try {
            val gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
            activeGatt = gatt

            if (!connectLatch.await(5, TimeUnit.SECONDS) || rxCharacteristic == null) {
                if (!mtuLatch.await(3, TimeUnit.SECONDS)) {
                    return Result.failure(IllegalStateException("GATT connection or service discovery timed out"))
                }
            }

            val charac = rxCharacteristic ?: return Result.failure(IllegalStateException("NUS RX Characteristic not found"))

            // 3. Transmit using Stop-and-Wait Write-With-Response
            val fullPayload = (payloadJson.trimEnd() + "\n").toByteArray(Charsets.UTF_8)
            val chunkSize = (negotiatedMtu - 3).coerceIn(20, 509)
            var bytesSent = 0

            Log.i(TAG, "Streaming ${fullPayload.size} bytes in chunks of $chunkSize...")

            for (i in fullPayload.indices step chunkSize) {
                val end = minOf(i + chunkSize, fullPayload.size)
                val chunk = fullPayload.copyOfRange(i, end)

                writeSuccess.set(false)
                val chunkLatch = CountDownLatch(1)

                // Set write type to DEFAULT (Write with response for flow control)
                charac.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    gatt.writeCharacteristic(charac, chunk, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)
                } else {
                    @Suppress("DEPRECATION")
                    charac.value = chunk
                    @Suppress("DEPRECATION")
                    gatt.writeCharacteristic(charac)
                }

                // Wait up to 1500ms for write ACK
                if (!writeAckLatch.await(1500, TimeUnit.MILLISECONDS)) {
                    Log.w(TAG, "Write acknowledgment timed out at offset $i")
                }
                bytesSent += chunk.size
            }

            Log.i(TAG, "Transmission complete: $bytesSent bytes sent.")
            return Result.success(bytesSent)

        } finally {
            // 4. Deterministic teardown: disconnect and close immediately
            activeGatt?.disconnect()
            activeGatt?.close()
        }
    }
}
