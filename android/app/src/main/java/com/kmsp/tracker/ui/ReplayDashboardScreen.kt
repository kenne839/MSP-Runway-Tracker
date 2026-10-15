package com.kmsp.tracker.ui

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kmsp.tracker.ble.NUS_SERVICE_UUID
import com.kmsp.tracker.net.ReplayApiClient
import com.kmsp.tracker.presence.PresenceEngine
import com.kmsp.tracker.presence.SyncMode
import com.kmsp.tracker.sync.ExactAlarmReceiver
import com.kmsp.tracker.sync.ReplaySyncExecutor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReplayDashboardScreen() {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE) }
    val scope = rememberCoroutineScope()

    // Initialize PresenceEngine mode from preferences
    LaunchedEffect(Unit) {
        PresenceEngine.init(context)
    }

    var backendUrl by remember {
        mutableStateOf(prefs.getString("backend_url", "http://192.168.0.213:8080") ?: "http://192.168.0.213:8080")
    }
    var apiToken by remember {
        mutableStateOf(prefs.getString("api_token", "") ?: "")
    }
    var selectedMode by remember {
        mutableStateOf(PresenceEngine.getMode())
    }

    // Reactive Compose states updated via SharedPreferences listener
    var lastSyncTs by remember { mutableStateOf(prefs.getLong("last_sync_timestamp", 0)) }
    var lastStatus by remember { mutableStateOf(prefs.getString("last_status", "Never Synced") ?: "Never Synced") }
    var lastBytesSent by remember { mutableStateOf(prefs.getInt("last_bytes_sent", 0)) }

    DisposableEffect(prefs) {
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { sp, key ->
            when (key) {
                "last_sync_timestamp" -> lastSyncTs = sp.getLong("last_sync_timestamp", 0)
                "last_status" -> lastStatus = sp.getString("last_status", "Never Synced") ?: "Never Synced"
                "last_bytes_sent" -> lastBytesSent = sp.getInt("last_bytes_sent", 0)
                "sync_mode" -> selectedMode = PresenceEngine.getMode()
            }
        }
        prefs.registerOnSharedPreferenceChangeListener(listener)
        onDispose {
            prefs.unregisterOnSharedPreferenceChangeListener(listener)
        }
    }

    var isSyncing by remember { mutableStateOf(false) }
    var syncProgressMessage by remember { mutableStateOf("") }
    var backendDiagnosticResult by remember { mutableStateOf("") }
    var bleDiagnosticResult by remember { mutableStateOf("") }
    var isScanningBle by remember { mutableStateOf(false) }

    val lastSyncStr = if (lastSyncTs > 0) {
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date(lastSyncTs))
    } else {
        "Never"
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text(
            text = "KMSP Runway Office Bridge",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold
        )

        // Presence / Mode Selector Card
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text = "Geofencing & Sync Policy", fontWeight = FontWeight.SemiBold)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(
                        selected = (selectedMode == SyncMode.HOME_TEST),
                        onClick = {
                            selectedMode = SyncMode.HOME_TEST
                            PresenceEngine.setMode(SyncMode.HOME_TEST, save = true, context = context)
                        }
                    )
                    Text("Home Test Mode (24/7 uninhibited)")
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(
                        selected = (selectedMode == SyncMode.OFFICE_PROD),
                        onClick = {
                            selectedMode = SyncMode.OFFICE_PROD
                            PresenceEngine.setMode(SyncMode.OFFICE_PROD, save = true, context = context)
                        }
                    )
                    Text("Office Mode (Work Hours + Ambient Wi-Fi)")
                }
            }
        }

        // Live Status Card
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(text = "Sync Telemetry Status", fontWeight = FontWeight.SemiBold)
                Text(text = "Last Sync: $lastSyncStr")
                Text(
                    text = "Last Result: $lastStatus",
                    color = when {
                        lastStatus.startsWith("SUCCESS") -> Color(0xFF2E7D32)
                        lastStatus.startsWith("BLE_ERROR") || lastStatus.startsWith("HTTP_ERROR") -> MaterialTheme.colorScheme.error
                        else -> MaterialTheme.colorScheme.onSurface
                    },
                    fontWeight = FontWeight.Medium
                )
                if (lastBytesSent > 0) {
                    Text(text = "Last Payload: $lastBytesSent bytes transferred")
                }
                Text(text = "Background Schedule: Every 30 mins (Active)")
            }
        }

        // Configuration Card
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text = "Connection Settings", fontWeight = FontWeight.SemiBold)
                OutlinedTextField(
                    value = backendUrl,
                    onValueChange = {
                        backendUrl = it
                        prefs.edit().putString("backend_url", it).apply()
                    },
                    label = { Text("Backend URL (Local IP or Cloudflare Tunnel)") },
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = apiToken,
                    onValueChange = {
                        apiToken = it
                        prefs.edit().putString("api_token", it).apply()
                    },
                    label = { Text("API Token (Optional X-KMSP-Token)") },
                    modifier = Modifier.fillMaxWidth()
                )
            }
        }

        // Manual Immediate Push Action
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        if (isSyncing) return@Button
                        isSyncing = true
                        syncProgressMessage = "Initiating push to desk board..."

                        scope.launch {
                            val result = withContext(Dispatchers.IO) {
                                ReplaySyncExecutor.executeSync(context, ignorePresence = true) { progress ->
                                    scope.launch(Dispatchers.Main) {
                                        syncProgressMessage = progress
                                    }
                                }
                            }

                            isSyncing = false
                            if (result.isSuccess) {
                                val bytes = result.getOrDefault(0)
                                syncProgressMessage = "SUCCESS: Deployed $bytes bytes to desk board!"
                                Toast.makeText(context, "Sync complete ($bytes bytes)!", Toast.LENGTH_SHORT).show()
                            } else {
                                val err = result.exceptionOrNull()?.message ?: "Unknown error"
                                syncProgressMessage = "FAILED: $err"
                                Toast.makeText(context, "Sync failed: $err", Toast.LENGTH_LONG).show()
                            }
                            ExactAlarmReceiver.scheduleNextSync(context)
                        }
                    },
                    enabled = !isSyncing,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    if (isSyncing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(text = "Syncing with Desk Board...")
                    } else {
                        Text(text = "Sync Now (Push to Desk Board)")
                    }
                }

                if (syncProgressMessage.isNotBlank()) {
                    Text(
                        text = syncProgressMessage,
                        style = MaterialTheme.typography.bodySmall,
                        color = when {
                            syncProgressMessage.startsWith("SUCCESS") -> Color(0xFF2E7D32)
                            syncProgressMessage.startsWith("FAILED") -> MaterialTheme.colorScheme.error
                            else -> MaterialTheme.colorScheme.primary
                        },
                        modifier = Modifier.padding(top = 4.dp)
                    )
                }
            }
        }

        // Diagnostics Card
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text = "Diagnostics & Bench Verification", fontWeight = FontWeight.SemiBold)

                // 1. Backend Connectivity Test
                Button(
                    onClick = {
                        scope.launch {
                            backendDiagnosticResult = "Testing connection to $backendUrl..."
                            withContext(Dispatchers.IO) {
                                val client = ReplayApiClient(backendUrl, apiToken)
                                val result = client.fetchReplayBatch(5)
                                backendDiagnosticResult = if (result.isSuccess) {
                                    val data = result.getOrNull() ?: ""
                                    "SUCCESS! Received ${data.length} bytes.\nSample: ${data.take(100)}..."
                                } else {
                                    "FAILED: ${result.exceptionOrNull()?.message}"
                                }
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("1. Test Backend (5G/Wi-Fi)")
                }

                if (backendDiagnosticResult.isNotBlank()) {
                    Text(
                        text = backendDiagnosticResult,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(start = 4.dp, bottom = 4.dp)
                    )
                }

                // 2. BLE Peripheral Discovery Test
                Button(
                    onClick = {
                        if (isScanningBle) return@Button
                        isScanningBle = true
                        bleDiagnosticResult = "Scanning for 'KMSP-Runway-Office' (6s)..."

                        scope.launch {
                            val scanResultText = withContext(Dispatchers.IO) {
                                testBleDiscovery(context)
                            }
                            bleDiagnosticResult = scanResultText
                            isScanningBle = false
                        }
                    },
                    enabled = !isScanningBle,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    if (isScanningBle) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("Scanning Nearby BLE...")
                    } else {
                        Text("2. Test BLE Discovery (Find Desk Board)")
                    }
                }

                if (bleDiagnosticResult.isNotBlank()) {
                    Text(
                        text = bleDiagnosticResult,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(start = 4.dp)
                    )
                }
            }
        }
    }
}

@SuppressLint("MissingPermission")
private fun testBleDiscovery(context: Context): String {
    val bluetoothManager = context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
    val adapter = bluetoothManager?.adapter
    if (adapter == null || !adapter.isEnabled) {
        return "FAILED: Bluetooth is disabled on phone."
    }

    val scanner = adapter.bluetoothLeScanner ?: return "FAILED: BLE Scanner unavailable."
    var foundDetails = "FAILED: Peripheral 'KMSP-Runway-Office' not found within 6s."
    val latch = CountDownLatch(1)

    val callback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult?) {
            val device = result?.device ?: return
            val record = result.scanRecord
            val name = record?.deviceName ?: device.name
            val serviceUuids = record?.serviceUuids
            val hasNusUuid = serviceUuids?.any { it.uuid == NUS_SERVICE_UUID } == true
            val matchesName = name != null && (name.contains("KMSP-Runway", ignoreCase = true) || name.contains("Office", ignoreCase = true))

            if (matchesName || hasNusUuid) {
                foundDetails = "SUCCESS! Found '$name'\nMAC: ${device.address} | RSSI: ${result.rssi} dBm"
                try {
                    scanner.stopScan(this)
                } catch (e: Exception) {
                    // Ignore
                }
                latch.countDown()
            }
        }

        override fun onScanFailed(errorCode: Int) {
            foundDetails = "FAILED: Scan failed with error code $errorCode"
            latch.countDown()
        }
    }

    val settings = ScanSettings.Builder()
        .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
        .build()

    try {
        scanner.startScan(null, settings, callback)
        latch.await(6, TimeUnit.SECONDS)
    } catch (e: SecurityException) {
        return "FAILED: Missing Bluetooth permission (${e.message})"
    } catch (e: Exception) {
        return "FAILED: ${e.message}"
    } finally {
        try {
            scanner.stopScan(callback)
        } catch (e: Exception) {
            // Ignore
        }
    }

    return foundDetails
}
