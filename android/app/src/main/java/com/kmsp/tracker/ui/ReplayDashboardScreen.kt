package com.kmsp.tracker.ui

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.os.ParcelUuid
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kmsp.tracker.ble.NUS_SERVICE_UUID
import com.kmsp.tracker.diagnostics.DiagnosticsRepository
import com.kmsp.tracker.diagnostics.FlightOpSummary
import com.kmsp.tracker.diagnostics.SyncDiagnosticEntry
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
    var lastDeviceName by remember { mutableStateOf(prefs.getString("last_device_name", "") ?: "") }
    var lastDeviceMac by remember { mutableStateOf(prefs.getString("last_device_mac", "") ?: "") }
    var lastAckResponse by remember { mutableStateOf(prefs.getString("last_ack_response", "") ?: "") }

    // Diagnostics Session & Log states
    var diagnosticEntries by remember { mutableStateOf(DiagnosticsRepository.getEntries(context)) }
    var sessionSummary by remember { mutableStateOf(DiagnosticsRepository.getSessionSummary(context)) }

    DisposableEffect(prefs) {
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { sp, key ->
            when (key) {
                "last_sync_timestamp" -> lastSyncTs = sp.getLong("last_sync_timestamp", 0)
                "last_status" -> lastStatus = sp.getString("last_status", "Never Synced") ?: "Never Synced"
                "last_bytes_sent" -> lastBytesSent = sp.getInt("last_bytes_sent", 0)
                "last_device_name" -> lastDeviceName = sp.getString("last_device_name", "") ?: ""
                "last_device_mac" -> lastDeviceMac = sp.getString("last_device_mac", "") ?: ""
                "last_ack_response" -> lastAckResponse = sp.getString("last_ack_response", "") ?: ""
                "sync_mode" -> selectedMode = PresenceEngine.getMode()
            }
        }
        val diagListener = {
            diagnosticEntries = DiagnosticsRepository.getEntries(context)
            sessionSummary = DiagnosticsRepository.getSessionSummary(context)
        }
        prefs.registerOnSharedPreferenceChangeListener(listener)
        DiagnosticsRepository.registerListener(diagListener)

        onDispose {
            prefs.unregisterOnSharedPreferenceChangeListener(listener)
            DiagnosticsRepository.unregisterListener(diagListener)
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
        // App Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "KMSP Runway Bridge",
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Bold
                )
                Text(
                    text = "App Build: v1.1.0 (Build 11) - Diagnostic Edition",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.SemiBold
                )
            }
        }

        // --- 1. Long-Run Test Hub Card ---
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
        ) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "🔬 Long-Run Test Hub",
                        fontWeight = FontWeight.Bold,
                        fontSize = 17.sp
                    )
                    Text(
                        text = "Session: ${sessionSummary.sessionDurationFormatted}",
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.primary
                    )
                }

                // High-Level Metrics 3x2 Grid
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = "Sync Success", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        Text(
                            text = "${sessionSummary.successfulSyncs}/${sessionSummary.totalAttempts} (${sessionSummary.successRatePct}%)",
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold,
                            color = if (sessionSummary.failedSyncs == 0 && sessionSummary.totalAttempts > 0) Color(0xFF2E7D32) else MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = "Missed / Delayed", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        Text(
                            text = "${sessionSummary.delayedSyncCount} delayed",
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold,
                            color = if (sessionSummary.delayedSyncCount > 0) MaterialTheme.colorScheme.error else Color(0xFF2E7D32)
                        )
                    }
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = "Flights Verified", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        Text(
                            text = "${sessionSummary.totalFlightsVerified} flights",
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.primary
                        )
                    }
                }

                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = "Battery Status", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        val deltaSign = if (sessionSummary.batteryNetDeltaPct >= 0) "+${sessionSummary.batteryNetDeltaPct}%" else "${sessionSummary.batteryNetDeltaPct}%"
                        Text(
                            text = "${sessionSummary.initialBatteryPct}% → ${sessionSummary.currentBatteryPct}% ($deltaSign)",
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = "Avg Execution", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        Text(
                            text = "${sessionSummary.avgExecutionDurationMs} ms",
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = "WakeLock Duty", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        Text(
                            text = "${String.format(Locale.US, "%.2f", sessionSummary.dutyCyclePct)}% (${sessionSummary.totalWakeLockDurationMs / 1000}s)",
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                }

                // Action Buttons: Reset Session & Copy Report
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    OutlinedButton(
                        onClick = {
                            DiagnosticsRepository.resetSession(context)
                            diagnosticEntries = DiagnosticsRepository.getEntries(context)
                            sessionSummary = DiagnosticsRepository.getSessionSummary(context)
                            Toast.makeText(context, "Test session reset. Starting fresh baseline.", Toast.LENGTH_SHORT).show()
                        },
                        modifier = Modifier.weight(1f)
                    ) {
                        Text(text = "Reset Baseline", fontSize = 12.sp)
                    }

                    Button(
                        onClick = {
                            val report = DiagnosticsRepository.generateMarkdownReport(context)
                            val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                            clipboard.setPrimaryClip(ClipData.newPlainText("KMSP Test Report", report))
                            Toast.makeText(context, "Full diagnostic report copied to clipboard!", Toast.LENGTH_SHORT).show()
                        },
                        modifier = Modifier.weight(1f)
                    ) {
                        Text(text = "📋 Copy Report", fontSize = 12.sp)
                    }
                }
            }
        }

        // --- 2. Manual Immediate Push Action ---
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        if (isSyncing) return@Button
                        isSyncing = true
                        syncProgressMessage = "Initiating push to desk board..."

                        scope.launch {
                            val result = withContext(Dispatchers.IO) {
                                ReplaySyncExecutor.executeSync(
                                    context = context,
                                    ignorePresence = true,
                                    triggerSource = "MANUAL_UI"
                                ) { progress ->
                                    scope.launch(Dispatchers.Main) {
                                        syncProgressMessage = progress
                                    }
                                }
                            }

                            isSyncing = false
                            if (result.isSuccess) {
                                val bytes = result.getOrDefault(0)
                                val ack = prefs.getString("last_ack_response", "") ?: ""
                                val targetName = prefs.getString("last_device_name", "KMSP-Runway-Office") ?: "KMSP-Runway-Office"
                                syncProgressMessage = "SUCCESS: Confirmed by $targetName ($bytes B)!\nACK: $ack"
                                Toast.makeText(context, "Sync complete ($bytes bytes)!", Toast.LENGTH_SHORT).show()
                            } else {
                                val err = result.exceptionOrNull()?.message ?: "Unknown error"
                                syncProgressMessage = "FAILED: $err"
                                Toast.makeText(context, "Sync failed: $err", Toast.LENGTH_LONG).show()
                            }
                            diagnosticEntries = DiagnosticsRepository.getEntries(context)
                            sessionSummary = DiagnosticsRepository.getSessionSummary(context)
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

                Text(
                    text = "Background Auto-Sync: Every 30 mins (Active via Exact RTC Wake Alarm)",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.outline
                )
            }
        }

        // --- 3. Chronological Sync History & Flight Audit ---
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    text = "📋 Sync History & Flight Audit (${diagnosticEntries.size} runs)",
                    fontWeight = FontWeight.Bold,
                    fontSize = 16.sp
                )

                if (diagnosticEntries.isEmpty()) {
                    Text(
                        text = "No sync runs recorded yet. Press 'Sync Now' above or wait for background alarm.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.outline
                    )
                } else {
                    diagnosticEntries.forEachIndexed { index, entry ->
                        val syncNumber = diagnosticEntries.size - index
                        DiagnosticEntryCard(syncNumber = syncNumber, entry = entry)
                    }
                }
            }
        }

        // --- 4. Presence & Mode Selector Card ---
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

        // --- 5. Connection Settings Card ---
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

        // --- 6. Connectivity Bench Diagnostics ---
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text = "Hardware Diagnostic Checks", fontWeight = FontWeight.SemiBold)

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
                        bleDiagnosticResult = "Scanning for 'KMSP-Runway-Office' (8s)..."

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

@Composable
fun DiagnosticEntryCard(syncNumber: Int, entry: SyncDiagnosticEntry) {
    var expanded by remember { mutableStateOf(false) }

    val statusColor = when {
        entry.isSuccess -> Color(0xFF2E7D32)
        entry.isIntervalDelayed -> Color(0xFFE65100)
        else -> MaterialTheme.colorScheme.error
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp)
        ) {
            // Header: Sync # and Status Badge
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "Sync #$syncNumber • ${entry.formattedTime}",
                    fontWeight = FontWeight.Bold,
                    fontSize = 14.sp
                )
                Text(
                    text = if (entry.isSuccess) "VERIFIED" else "FAILED",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = statusColor
                )
            }

            // Trigger & Interval Stats
            val intervalStr = if (entry.elapsedSinceLastSyncMs > 0) {
                val mins = entry.elapsedSinceLastSyncMs / 60000
                val secs = (entry.elapsedSinceLastSyncMs % 60000) / 1000
                "${mins}m ${secs}s (jitter: ${if (entry.intervalJitterSec >= 0) "+${entry.intervalJitterSec}s" else "${entry.intervalJitterSec}s"})"
            } else {
                "First Sync Run"
            }

            Text(
                text = "Trigger: ${entry.triggerSource} | Interval: $intervalStr",
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.outline
            )

            // Power & Duration
            Text(
                text = "Duration: ${entry.executionDurationMs}ms (WakeLock: ${entry.wakeLockDurationMs}ms) | Battery: ${entry.batteryPctBefore}% → ${entry.batteryPctAfter}%",
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))

            // Pipeline Step 1: Backend Capture
            Text(
                text = "1. Pi Capture: Batch ${entry.batchId} (${entry.flightCount} flights, ${entry.wirePayloadBytes} B, chrono: ${if (entry.isChronological) "PASS" else "FAIL"})",
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium
            )

            // Pipeline Step 2: BLE Transport
            Text(
                text = "2. BLE Push: ${entry.connectionMethod} to ${entry.targetMac} (${entry.bleBytesTransferred} B, MTU ${entry.bleMtu}, connect: ${entry.bleConnectDurationMs}ms)",
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium
            )

            // Pipeline Step 3: Hardware ACK Verification
            if (entry.ackReceived) {
                Text(
                    text = "3. Desk Board ACK: Batch match: ${if (entry.isBatchVerified) "PASS" else "FAIL"} | Ops match: ${if (entry.isOpsVerified) "PASS" else "FAIL"} | Free Heap: ${entry.ackFreeHeap / 1024} KB",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    color = if (entry.isBatchVerified && entry.isOpsVerified) Color(0xFF2E7D32) else MaterialTheme.colorScheme.error
                )
            }

            if (entry.flightSummaryText.isNotBlank()) {
                Text(
                    text = "Flights: ${entry.flightSummaryText}",
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(top = 2.dp)
                )
            }

            if (entry.errorMessage != null) {
                Text(
                    text = "Error: ${entry.errorMessage}",
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.error,
                    fontWeight = FontWeight.SemiBold
                )
            }

            // Expandable Flight List Toggle
            if (entry.flights.isNotEmpty()) {
                Text(
                    text = if (expanded) "▲ Hide Full Flight Schedule" else "▼ View All ${entry.flights.size} Flights",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier
                        .clickable { expanded = !expanded }
                        .padding(top = 4.dp)
                )

                if (expanded) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(4.dp))
                            .padding(8.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        entry.flights.forEach { flight ->
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween
                            ) {
                                Text(
                                    text = "+${flight.offsetSec}s  ${flight.callsign} (${flight.flightLabel})",
                                    fontSize = 11.sp,
                                    fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.SemiBold
                                )
                                Text(
                                    text = "RW ${flight.runway} ${flight.action} [${flight.aircraftType}]",
                                    fontSize = 11.sp,
                                    fontFamily = FontFamily.Monospace,
                                    color = if (flight.action == "TAKEOFF") Color(0xFF1976D2) else Color(0xFF388E3C)
                                )
                            }
                        }
                    }
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

    val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
    val savedMac = prefs.getString("last_device_mac", null)?.trim()

    val scanner = adapter.bluetoothLeScanner ?: return "FAILED: BLE Scanner unavailable."
    var foundDetails = "FAILED: Peripheral 'KMSP-Runway-Office' not found within 8s."
    val latch = CountDownLatch(1)

    val callback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult?) {
            val device = result?.device ?: return
            val record = result.scanRecord
            val name = record?.deviceName ?: device.name
            val serviceUuids = record?.serviceUuids
            val hasNusUuid = serviceUuids?.any { it.uuid == NUS_SERVICE_UUID } == true
            val matchesName = name != null && name.contains("KMSP-Runway", ignoreCase = true)
            val matchesMac = !savedMac.isNullOrEmpty() && device.address.equals(savedMac, ignoreCase = true)

            if (matchesName || hasNusUuid || matchesMac) {
                val displayName = if (!name.isNullOrBlank()) name else "KMSP-Runway-Office"
                foundDetails = "SUCCESS! Found '$displayName'\nMAC: ${device.address} | RSSI: ${result.rssi} dBm"
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

    val filters = ArrayList<ScanFilter>()
    if (!savedMac.isNullOrEmpty() && BluetoothAdapter.checkBluetoothAddress(savedMac)) {
        filters.add(ScanFilter.Builder().setDeviceAddress(savedMac).build())
    }
    filters.add(ScanFilter.Builder().setServiceUuid(ParcelUuid(NUS_SERVICE_UUID)).build())
    filters.add(ScanFilter.Builder().setDeviceName("KMSP-Runway-Office").build())

    val settings = ScanSettings.Builder()
        .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
        .build()

    try {
        scanner.startScan(filters, settings, callback)
        latch.await(8, TimeUnit.SECONDS)
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
