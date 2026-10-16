package com.kmsp.tracker.ble

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Build
import android.util.Log
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

private const val TAG = "KMSP_BLE"

val NUS_SERVICE_UUID: UUID = UUID.fromString("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
val NUS_RX_UUID: UUID = UUID.fromString("6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
val NUS_TX_UUID: UUID = UUID.fromString("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")
val CLIENT_CONFIG_DESCRIPTOR_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

data class BleTransferResult(
    val bytesSent: Int,
    val deviceName: String,
    val deviceAddress: String,
    val appAck: String
)

class BleNusClient(private val context: Context) {

    private val bluetoothAdapter: BluetoothAdapter? by lazy {
        val manager = context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
        manager?.adapter
    }

    @SuppressLint("MissingPermission")
    fun transmitBatchPayload(
        payloadJson: String,
        timeoutSec: Long = 10,
        onProgress: ((String) -> Unit)? = null
    ): Result<BleTransferResult> {
        val adapter = bluetoothAdapter
        if (adapter == null || !adapter.isEnabled) {
            return Result.failure(IllegalStateException("Bluetooth is disabled or unavailable on this device"))
        }

        val scanner = adapter.bluetoothLeScanner
            ?: return Result.failure(IllegalStateException("BLE Scanner unavailable"))

        Log.i(TAG, "Scanning for KMSP-Runway-Office peripheral...")
        onProgress?.invoke("Scanning for KMSP-Runway-Office BLE peripheral...")

        var targetDevice: BluetoothDevice? = null
        val scanLatch = CountDownLatch(1)

        val scanCallback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult?) {
                val device = result?.device ?: return
                val record = result.scanRecord
                val name = record?.deviceName ?: device.name
                val serviceUuids = record?.serviceUuids
                val hasNusUuid = serviceUuids?.any { it.uuid == NUS_SERVICE_UUID } == true
                val matchesName = name != null && name.contains("KMSP-Runway", ignoreCase = true)

                Log.d(TAG, "Scanned BLE device: name='$name', addr=${device.address}, rssi=${result.rssi}")

                if (matchesName || hasNusUuid) {
                    Log.i(TAG, "Found target peripheral: '$name' (${device.address}, RSSI: ${result.rssi})")
                    targetDevice = device
                    try {
                        scanner.stopScan(this)
                    } catch (e: Exception) {
                        Log.w(TAG, "Error stopping scan in callback: ${e.message}")
                    }
                    scanLatch.countDown()
                }
            }

            override fun onScanFailed(errorCode: Int) {
                Log.w(TAG, "BLE scan failed with error code $errorCode")
                scanLatch.countDown()
            }
        }

        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .setReportDelay(0)
            .build()

        // 1. Scan with up to 8-second discovery timeout (pass null filters for universal OEM compatibility)
        try {
            scanner.startScan(null, settings, scanCallback)
            scanLatch.await(8, TimeUnit.SECONDS)
        } catch (e: SecurityException) {
            return Result.failure(IllegalStateException("Bluetooth scan permission missing: ${e.message}"))
        } catch (e: Exception) {
            Log.w(TAG, "Exception during startScan: ${e.message}")
        } finally {
            try {
                scanner.stopScan(scanCallback)
            } catch (e: Exception) {
                // Ignore if already stopped
            }
        }

        val device = targetDevice ?: return Result.failure(IllegalStateException("Peripheral 'KMSP-Runway-Office' not found. Ensure ESP32 desk board is powered on nearby."))

        onProgress?.invoke("Found ${device.name ?: "ESP32"} (${device.address})! Connecting...")

        // 2. Connect GATT directly (autoConnect = false, TRANSPORT_LE)
        val connectLatch = CountDownLatch(1)
        val mtuLatch = CountDownLatch(1)
        val servicesDiscoveredLatch = CountDownLatch(1)
        val cccdLatch = CountDownLatch(1)
        val ackLatch = CountDownLatch(1)
        val writeSuccess = AtomicBoolean(false)
        var currentWriteLatch: CountDownLatch? = null
        var negotiatedMtu = 23
        var activeGatt: BluetoothGatt? = null
        var rxCharacteristic: BluetoothGattCharacteristic? = null
        var isConnected = false
        var appAckResponse = ""

        val gattCallback = object : BluetoothGattCallback() {
            override fun onConnectionStateChange(gatt: BluetoothGatt?, status: Int, newState: Int) {
                Log.d(TAG, "GATT state change: status=$status, newState=$newState")
                if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                    isConnected = true
                    activeGatt = gatt
                    Log.i(TAG, "Connected to GATT server. Requesting CONNECTION_PRIORITY_HIGH...")
                    gatt?.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)

                    // Request MTU 512 for fast batch streaming
                    val mtuReq = gatt?.requestMtu(512) ?: false
                    if (!mtuReq) {
                        Log.w(TAG, "requestMtu returned false, discovering services directly")
                        gatt?.discoverServices()
                    }
                    connectLatch.countDown()
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                    Log.i(TAG, "GATT disconnected cleanly (status=$status).")
                    isConnected = false
                    connectLatch.countDown()
                    mtuLatch.countDown()
                    servicesDiscoveredLatch.countDown()
                    currentWriteLatch?.countDown()
                    ackLatch.countDown()
                } else if (status != BluetoothGatt.GATT_SUCCESS) {
                    Log.w(TAG, "GATT error status=$status, newState=$newState")
                    connectLatch.countDown()
                    mtuLatch.countDown()
                    servicesDiscoveredLatch.countDown()
                    currentWriteLatch?.countDown()
                    ackLatch.countDown()
                }
            }

            override fun onMtuChanged(gatt: BluetoothGatt?, mtu: Int, status: Int) {
                Log.i(TAG, "MTU negotiated: $mtu (status: $status)")
                negotiatedMtu = if (status == BluetoothGatt.GATT_SUCCESS) mtu else 23
                mtuLatch.countDown()
                gatt?.discoverServices()
            }

            override fun onServicesDiscovered(gatt: BluetoothGatt?, status: Int) {
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    val service = gatt?.getService(NUS_SERVICE_UUID)
                    rxCharacteristic = service?.getCharacteristic(NUS_RX_UUID)
                    val txCharacteristic = service?.getCharacteristic(NUS_TX_UUID)

                    // Enable closed-loop Application ACK notifications on NUS TX
                    if (txCharacteristic != null) {
                        gatt?.setCharacteristicNotification(txCharacteristic, true)
                        val cccd = txCharacteristic.getDescriptor(CLIENT_CONFIG_DESCRIPTOR_UUID)
                        if (cccd != null) {
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                                gatt?.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                            } else {
                                @Suppress("DEPRECATION")
                                cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                                @Suppress("DEPRECATION")
                                gatt?.writeDescriptor(cccd)
                            }
                            Log.i(TAG, "Enabled NUS TX notifications for closed-loop ACK")
                        }
                    }

                    Log.i(TAG, "Services discovered. RX ready: ${rxCharacteristic != null}, TX ready: ${txCharacteristic != null}")
                } else {
                    Log.w(TAG, "Services discovery failed with status $status")
                }
                servicesDiscoveredLatch.countDown()
            }

            override fun onDescriptorWrite(
                gatt: BluetoothGatt?,
                descriptor: BluetoothGattDescriptor?,
                status: Int
            ) {
                Log.i(TAG, "CCCD descriptor write finished with status $status")
                cccdLatch.countDown()
            }

            override fun onCharacteristicWrite(
                gatt: BluetoothGatt?,
                characteristic: BluetoothGattCharacteristic?,
                status: Int
            ) {
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    writeSuccess.set(true)
                } else {
                    Log.w(TAG, "Write failed with status $status")
                    writeSuccess.set(false)
                }
                currentWriteLatch?.countDown()
            }

            override fun onCharacteristicChanged(
                gatt: BluetoothGatt,
                characteristic: BluetoothGattCharacteristic,
                value: ByteArray
            ) {
                if (characteristic.uuid == NUS_TX_UUID) {
                    val ackStr = String(value, Charsets.UTF_8).trim()
                    Log.i(TAG, "Application ACK received from desk board: $ackStr")
                    appAckResponse = ackStr
                    ackLatch.countDown()
                }
            }

            @Deprecated("Deprecated in Java")
            override fun onCharacteristicChanged(
                gatt: BluetoothGatt?,
                characteristic: BluetoothGattCharacteristic?
            ) {
                if (characteristic?.uuid == NUS_TX_UUID) {
                    val ackStr = characteristic.getStringValue(0)?.trim() ?: ""
                    Log.i(TAG, "Application ACK received from desk board (legacy): $ackStr")
                    appAckResponse = ackStr
                    ackLatch.countDown()
                }
            }
        }

        try {
            val gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
            activeGatt = gatt

            if (!connectLatch.await(7, TimeUnit.SECONDS) || !isConnected) {
                return Result.failure(IllegalStateException("GATT connection failed or timed out (7s)"))
            }

            onProgress?.invoke("Connected! Negotiating MTU & discovering services...")
            mtuLatch.await(2, TimeUnit.SECONDS)

            if (!servicesDiscoveredLatch.await(5, TimeUnit.SECONDS) || rxCharacteristic == null) {
                return Result.failure(IllegalStateException("GATT service discovery timed out or NUS RX characteristic missing"))
            }

            // Wait for CCCD notification registration to settle before writing characteristics
            cccdLatch.await(1500, TimeUnit.MILLISECONDS)

            val charac = rxCharacteristic ?: return Result.failure(IllegalStateException("NUS RX Characteristic not found"))

            // 3. Minify JSON: strip internal whitespace and newlines so the only '\n' is the end-of-frame delimiter
            val minifiedJson = try {
                JSONObject(payloadJson).toString()
            } catch (e: Exception) {
                payloadJson.replace("\r", "").replace("\n", " ").trim()
            }

            val fullPayload = (minifiedJson.trimEnd() + "\n").toByteArray(Charsets.UTF_8)
            val chunkSize = minOf((negotiatedMtu - 3).coerceAtLeast(20), 240)
            var bytesSent = 0
            val totalBytes = fullPayload.size

            Log.i(TAG, "Streaming $totalBytes bytes in chunks of $chunkSize...")
            onProgress?.invoke("Streaming $totalBytes bytes to desk board...")

            for (i in fullPayload.indices step chunkSize) {
                val end = minOf(i + chunkSize, totalBytes)
                val chunk = fullPayload.copyOfRange(i, end)

                writeSuccess.set(false)
                val chunkLatch = CountDownLatch(1)
                currentWriteLatch = chunkLatch

                charac.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                val initiated = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    gatt.writeCharacteristic(charac, chunk, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS
                } else {
                    @Suppress("DEPRECATION")
                    charac.value = chunk
                    @Suppress("DEPRECATION")
                    gatt.writeCharacteristic(charac)
                }

                if (!initiated) {
                    Log.w(TAG, "Failed to initiate write for chunk at offset $i")
                    return Result.failure(IllegalStateException("GATT write failed to initiate at offset $i"))
                }

                if (!chunkLatch.await(2000, TimeUnit.MILLISECONDS)) {
                    Log.w(TAG, "Write acknowledgment timed out at offset $i")
                    return Result.failure(IllegalStateException("GATT write ACK timed out at offset $i"))
                }

                if (!writeSuccess.get()) {
                    Log.w(TAG, "GATT write rejected with error at offset $i")
                    return Result.failure(IllegalStateException("GATT write rejected by peripheral at offset $i"))
                }

                bytesSent += chunk.size
                onProgress?.invoke("Streaming payload: $bytesSent / $totalBytes bytes")
            }

            // 4. Closed-loop verification: Await Application ACK notification from Desk Board firmware
            onProgress?.invoke("Payload sent ($bytesSent B). Awaiting firmware Application ACK...")
            val ackReceived = ackLatch.await(5, TimeUnit.SECONDS)
            if (!ackReceived) {
                Log.w(TAG, "Desk board application ACK timed out")
                return Result.failure(IllegalStateException("Payload sent ($bytesSent B), but Desk Board application ACK timed out."))
            }

            if (!appAckResponse.contains("\"status\":\"ACK\"")) {
                Log.w(TAG, "Desk board rejected batch: $appAckResponse")
                return Result.failure(IllegalStateException("Desk Board rejected batch: $appAckResponse"))
            }

            Log.i(TAG, "Closed-loop verification successful: $appAckResponse")
            val targetName = device.name ?: "KMSP-Runway-Office"
            onProgress?.invoke("SUCCESS: Confirmed by $targetName ($appAckResponse)!")
            return Result.success(BleTransferResult(bytesSent, targetName, device.address, appAckResponse))

        } catch (e: SecurityException) {
            return Result.failure(IllegalStateException("Bluetooth permission error during transfer: ${e.message}"))
        } catch (e: Exception) {
            return Result.failure(e)
        } finally {
            // 5. Deterministic teardown: disconnect and close
            try {
                activeGatt?.disconnect()
                Thread.sleep(150)
            } catch (e: Exception) {
                // ignore
            } finally {
                activeGatt?.close()
                activeGatt = null
            }
        }
    }
}
