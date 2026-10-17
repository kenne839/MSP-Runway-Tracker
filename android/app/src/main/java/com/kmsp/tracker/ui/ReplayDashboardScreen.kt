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
import com.kmsp.tracker.presence.PresenceStatus
import com.kmsp.tracker.presence.SyncMode
import com.kmsp.tracker.presence.WifiHelper
import com.kmsp.tracker.sync.ExactAlarmReceiver
import com.kmsp.tracker.sync.ReplaySyncExecutor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
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
    var autoSyncEnabled by remember {
        mutableStateOf(prefs.getBoolean("auto_sync_enabled", true))
    }
    var nextSyncTargetMs by remember {
        mutableStateOf(prefs.getLong("next_sync_target_ms", 0L))
    }
    var homeWifiSsid by remember {
        mutableStateOf(prefs.getString("home_wifi_ssid", "") ?: "")
    }
    var officeWifiSsid by remember {
        mutableStateOf(PresenceEngine.getOfficeSsid(context))
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

    // Live ticking timer for countdown and presence refresh
    var currentTimeMs by remember { mutableStateOf(System.currentTimeMillis()) }
    var presenceStatus by remember { mutableStateOf(PresenceEngine.getPresenceStatus(context)) }

    LaunchedEffect(Unit) {
        while (true) {
            delay(1000L)
            currentTimeMs = System.currentTimeMillis()
            presenceStatus = PresenceEngine.getPresenceStatus(context)
        }
    }

    DisposableEffect(prefs) {
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { sp, key ->
            when (key) {
                "last_sync_timestamp" -> lastSyncTs = sp.getLong("last_sync_timestamp", 0)
                "last_status" -> lastStatus = sp.getString("last_status", "Never Synced") ?: "Never Synced"
                "last_bytes_sent" -> lastBytesSent = sp.getInt("last_bytes_sent", 0)
                "last_device_name" -> lastDeviceName = sp.getString("last_device_name", "") ?: ""
                "last_device_mac" -> lastDeviceMac = sp.getString("last_device_mac", "") ?: ""
                "last_ack_response" -> lastAckResponse = sp.getString("last_ack_response", "") ?: ""
                "sync_mode" -> {
                    selectedMode = PresenceEngine.getMode()
                    presenceStatus = PresenceEngine.getPresenceStatus(context)
                }
                "auto_sync_enabled" -> autoSyncEnabled = sp.getBoolean("auto_sync_enabled", true)
                "next_sync_target_ms" -> nextSyncTargetMs = sp.getLong("next_sync_target_ms", 0L)
                "home_wifi_ssid" -> {
                    homeWifiSsid = sp.getString("home_wifi_ssid", "") ?: ""
                    presenceStatus = PresenceEngine.getPresenceStatus(context)
                }
                "office_wifi_ssid" -> {
                    officeWifiSsid = PresenceEngine.getOfficeSsid(context)
                    presenceStatus = PresenceEngine.getPresenceStatus(context)
                }
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
                    text = "App Build: v1.2.1 (Build 13) - Office & Home Geofencing",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.SemiBold
                )
            }
        }

        // =====================================================================
        // --- 1. Master Auto-Sync Controller & Live State Card ---
        // =====================================================================
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant
            )
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                // Top row: Section Title + Live State Badge
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "🎮 Auto-Sync Scheduler",
                        fontWeight = FontWeight.Bold,
                        fontSize = 17.sp
                    )

                    // Master App State Badge
                    val (badgeText, badgeBg, badgeFg) = when {
                        isSyncing -> Triple("🔄 SYNCING...", Color(0xFF1976D2), Color.White)
                        !autoSyncEnabled -> Triple("⏹ STOPPED", Color(0xFF757575), Color.White)
                        !presenceStatus.isPermitted -> Triple("⏸ INHIBITED (AWAY)", Color(0xFFE65100), Color.White)
                        else -> Triple("▶ ACTIVE (30M)", Color(0xFF2E7D32), Color.White)
                    }

                    Surface(
                        shape = RoundedCornerShape(12.dp),
                        color = badgeBg,
                        contentColor = badgeFg,
                        modifier = Modifier.padding(start = 8.dp)
                    ) {
                        Text(
                            text = badgeText,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp)
                        )
                    }
                }

                // Countdown / Next Sync Display
                val remainingMs = nextSyncTargetMs - currentTimeMs
                val countdownText = when {
                    !autoSyncEnabled -> "Auto-sync paused — Tap 'Start Auto-Sync' to resume"
                    nextSyncTargetMs == 0L -> "No sync scheduled — Starting first interval"
                    remainingMs > 0 -> {
                        val mins = (remainingMs / 1000) / 60
                        val secs = (remainingMs / 1000) % 60
                        val targetTimeStr = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(nextSyncTargetMs))
                        if (!presenceStatus.isPermitted) {
                            "Inhibited: Next check in ${mins}m ${secs}s (at $targetTimeStr) • Outside geofence"
                        } else {
                            "Next sync in: ${mins}m ${secs}s (at $targetTimeStr)"
                        }
                    }
                    else -> "Sync due now / In progress..."
                }

                Text(
                    text = countdownText,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Medium,
                    color = if (!autoSyncEnabled) {
                        MaterialTheme.colorScheme.outline
                    } else if (!presenceStatus.isPermitted) {
                        Color(0xFFE65100)
                    } else {
                        Color(0xFF2E7D32)
                    }
                )

                HorizontalDivider()

                // Dual Action Buttons: [Start/Stop Auto-Sync] and [Sync Now]
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    // Start/Stop Button
                    if (autoSyncEnabled) {
                        OutlinedButton(
                            onClick = {
                                ExactAlarmReceiver.stopAutoSync(context)
                                autoSyncEnabled = false
                                nextSyncTargetMs = 0L
                                Toast.makeText(context, "Auto-sync stopped. No background syncs will run.", Toast.LENGTH_SHORT).show()
                            },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.outlinedButtonColors(
                                contentColor = MaterialTheme.colorScheme.error
                            )
                        ) {
                            Text("⏹ Stop Auto-Sync", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                        }
                    } else {
                        Button(
                            onClick = {
                                ExactAlarmReceiver.startAutoSync(context)
                                autoSyncEnabled = true
                                nextSyncTargetMs = ExactAlarmReceiver.getNextSyncTargetMs(context)
                                Toast.makeText(context, "Auto-sync started! Next sync scheduled in 30 minutes.", Toast.LENGTH_SHORT).show()
                            },
                            modifier = Modifier.weight(1f),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Color(0xFF2E7D32)
                            )
                        ) {
                            Text("▶ Start Auto-Sync", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                        }
                    }

                    // Push Immediate "Sync Now" Button
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
                                if (autoSyncEnabled) {
                                    ExactAlarmReceiver.scheduleNextSync(context)
                                    nextSyncTargetMs = ExactAlarmReceiver.getNextSyncTargetMs(context)
                                }
                            }
                        },
                        enabled = !isSyncing,
                        modifier = Modifier.weight(1f)
                    ) {
                        if (isSyncing) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.onPrimary
                            )
                            Spacer(modifier = Modifier.width(6.dp))
                            Text(text = "Syncing...", fontSize = 13.sp)
                        } else {
                            Text(text = "🚀 Sync Now", fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                        }
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
                        modifier = Modifier.padding(top = 2.dp)
                    )
                }
            }
        }

        // =====================================================================
        // --- 2. Geofencing & Wi-Fi Presence Policy Card ---
        // =====================================================================
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    text = "🛰️ Geofencing & Network Presence",
                    fontWeight = FontWeight.Bold,
                    fontSize = 17.sp
                )

                // Selectable Policy Mode Radio Buttons
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth().clickable {
                            selectedMode = SyncMode.HOME_GEOFENCE
                            PresenceEngine.setMode(SyncMode.HOME_GEOFENCE, save = true, context = context)
                            presenceStatus = PresenceEngine.getPresenceStatus(context)
                        }
                    ) {
                        RadioButton(
                            selected = (selectedMode == SyncMode.HOME_GEOFENCE),
                            onClick = {
                                selectedMode = SyncMode.HOME_GEOFENCE
                                PresenceEngine.setMode(SyncMode.HOME_GEOFENCE, save = true, context = context)
                                presenceStatus = PresenceEngine.getPresenceStatus(context)
                            }
                        )
                        Column {
                            Text("Home Geofence Mode", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Text("Matches Home Wi-Fi SSID; suppresses sync on cellular / away", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        }
                    }

                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth().clickable {
                            selectedMode = SyncMode.OFFICE_PROD
                            PresenceEngine.setMode(SyncMode.OFFICE_PROD, save = true, context = context)
                            presenceStatus = PresenceEngine.getPresenceStatus(context)
                        }
                    ) {
                        RadioButton(
                            selected = (selectedMode == SyncMode.OFFICE_PROD),
                            onClick = {
                                selectedMode = SyncMode.OFFICE_PROD
                                PresenceEngine.setMode(SyncMode.OFFICE_PROD, save = true, context = context)
                                presenceStatus = PresenceEngine.getPresenceStatus(context)
                            }
                        )
                        Column {
                            Text("Office Mode", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Text("Enforces Mon-Fri 07:30-18:00 + Office Wi-Fi beacon", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        }
                    }

                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth().clickable {
                            selectedMode = SyncMode.UNINHIBITED_BENCH
                            PresenceEngine.setMode(SyncMode.UNINHIBITED_BENCH, save = true, context = context)
                            presenceStatus = PresenceEngine.getPresenceStatus(context)
                        }
                    ) {
                        RadioButton(
                            selected = (selectedMode == SyncMode.UNINHIBITED_BENCH),
                            onClick = {
                                selectedMode = SyncMode.UNINHIBITED_BENCH
                                PresenceEngine.setMode(SyncMode.UNINHIBITED_BENCH, save = true, context = context)
                                presenceStatus = PresenceEngine.getPresenceStatus(context)
                            }
                        )
                        Column {
                            Text("Bench Test Mode (24/7 Uninhibited)", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Text("Bypasses all presence checks for lab verification", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        }
                    }
                }

                HorizontalDivider()

                // Target Home SSID configuration (Visible if in HOME_GEOFENCE mode)
                if (selectedMode == SyncMode.HOME_GEOFENCE) {
                    Text(
                        text = "Home Wi-Fi Configuration",
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        OutlinedTextField(
                            value = homeWifiSsid,
                            onValueChange = {
                                homeWifiSsid = it
                                PresenceEngine.setHomeSsid(context, it)
                                presenceStatus = PresenceEngine.getPresenceStatus(context)
                            },
                            label = { Text("Target Home Wi-Fi SSID") },
                            placeholder = { Text("e.g. MyHomeNetwork") },
                            modifier = Modifier.weight(1f),
                            singleLine = true
                        )

                        Button(
                            onClick = {
                                val currentSsid = WifiHelper.getCurrentSsid(context)
                                if (currentSsid.isNotBlank()) {
                                    homeWifiSsid = currentSsid
                                    PresenceEngine.setHomeSsid(context, currentSsid)
                                    presenceStatus = PresenceEngine.getPresenceStatus(context)
                                    Toast.makeText(context, "Target Home Wi-Fi set to '$currentSsid'", Toast.LENGTH_SHORT).show()
                                } else if (!WifiHelper.isWifiConnected(context)) {
                                    Toast.makeText(context, "Cannot read SSID: Phone is on Cellular Data. Connect to Home Wi-Fi first.", Toast.LENGTH_LONG).show()
                                } else {
                                    Toast.makeText(context, "Wi-Fi connected but SSID is hidden. Ensure Android Location toggle is ON, or enter SSID manually.", Toast.LENGTH_LONG).show()
                                }
                            },
                            modifier = Modifier.padding(top = 6.dp)
                        ) {
                            Text("Set Current", fontSize = 12.sp)
                        }
                    }
                }

                // Target Office SSID / Keywords configuration (Visible if in OFFICE_PROD mode)
                if (selectedMode == SyncMode.OFFICE_PROD) {
                    Text(
                        text = "Office Wi-Fi Configuration",
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 14.sp
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        OutlinedTextField(
                            value = officeWifiSsid,
                            onValueChange = {
                                officeWifiSsid = it
                                PresenceEngine.setOfficeSsid(context, it)
                                presenceStatus = PresenceEngine.getPresenceStatus(context)
                            },
                            label = { Text("Target Office SSID / Keywords") },
                            placeholder = { Text("e.g. AcmeNet, Acme-Guest") },
                            modifier = Modifier.weight(1f),
                            singleLine = true
                        )

                        Button(
                            onClick = {
                                val currentSsid = WifiHelper.getCurrentSsid(context)
                                if (currentSsid.isNotBlank()) {
                                    officeWifiSsid = currentSsid
                                    PresenceEngine.setOfficeSsid(context, currentSsid)
                                    presenceStatus = PresenceEngine.getPresenceStatus(context)
                                    Toast.makeText(context, "Target Office Wi-Fi set to '$currentSsid'", Toast.LENGTH_SHORT).show()
                                } else if (!WifiHelper.isWifiConnected(context)) {
                                    Toast.makeText(context, "Cannot read SSID: Phone is on Cellular Data. Connect to Office Wi-Fi first, or type name manually.", Toast.LENGTH_LONG).show()
                                } else {
                                    Toast.makeText(context, "Wi-Fi connected but SSID is hidden. Ensure Android Location toggle is ON, or enter SSID manually.", Toast.LENGTH_LONG).show()
                                }
                            },
                            modifier = Modifier.padding(top = 6.dp)
                        ) {
                            Text("Set Current", fontSize = 12.sp)
                        }
                    }

                    Text(
                        text = "Matches active Wi-Fi or ambient beacons in radio range (comma-separated).",
                        fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.outline
                    )
                }

                // Live Geofence Policy Decision Box
                val isPermitted = presenceStatus.isPermitted
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = if (isPermitted) Color(0xFFE8F5E9) else Color(0xFFFFF3E0),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Text(
                                text = if (isPermitted) "✅ GEOFENCE: PERMITTED" else "⚠️ GEOFENCE: INHIBITED",
                                fontWeight = FontWeight.Bold,
                                fontSize = 12.sp,
                                color = if (isPermitted) Color(0xFF1B5E20) else Color(0xFFBF360C)
                            )
                        }
                        Text(
                            text = presenceStatus.reason,
                            fontSize = 12.sp,
                            color = if (isPermitted) Color(0xFF2E7D32) else Color(0xFFE65100)
                        )
                        val connInfo = buildString {
                            append("Network: ")
                            if (presenceStatus.isWifiConnected) {
                                append("Wi-Fi")
                                if (presenceStatus.currentSsid.isNotBlank()) {
                                    append(" ('${presenceStatus.currentSsid}')")
                                }
                                if (presenceStatus.localIp.isNotBlank()) {
                                    append(" [${presenceStatus.localIp}]")
                                }
                            } else if (presenceStatus.isCellularConnected) {
                                append("Mobile Cellular (LTE/5G)")
                            } else {
                                append("Disconnected")
                            }
                        }
                        Text(
                            text = connInfo,
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.outline
                        )
                    }
                }
            }
        }

        // =====================================================================
        // --- 3. Long-Run Test Hub Card ---
        // =====================================================================
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

                // High-Level Metrics Grid
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
                        Text(text = "Inhibited (Away)", fontSize = 11.sp, color = MaterialTheme.colorScheme.outline)
                        Text(
                            text = "${sessionSummary.skippedCount} skipped",
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold,
                            color = if (sessionSummary.skippedCount > 0) Color(0xFFE65100) else MaterialTheme.colorScheme.onSurfaceVariant
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

        // =====================================================================
        // --- 4. Chronological Sync History & Flight Audit ---
        // =====================================================================
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

        // =====================================================================
        // --- 5. Connection Settings Card ---
        // =====================================================================
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text = "⚙️ Connection Settings", fontWeight = FontWeight.SemiBold)
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

        // =====================================================================
        // --- 6. Connectivity Bench Diagnostics ---
        // =====================================================================
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text = "🛠️ Hardware Diagnostic Checks", fontWeight = FontWeight.SemiBold)

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
    val isSkipped = entry.statusSummary.startsWith("SKIPPED")

    val statusColor = when {
        entry.isSuccess -> Color(0xFF2E7D32)
        isSkipped -> Color(0xFFE65100)
        entry.isIntervalDelayed -> Color(0xFFE65100)
        else -> MaterialTheme.colorScheme.error
    }

    val statusBadge = when {
        entry.isSuccess -> "VERIFIED"
        isSkipped -> "SKIPPED (AWAY)"
        else -> "FAILED"
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
                    text = statusBadge,
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

            if (isSkipped) {
                Text(
                    text = "Geofence Policy: ${entry.statusSummary}",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    color = Color(0xFFE65100)
                )
            } else {
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
