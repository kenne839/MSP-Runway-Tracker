package com.kmsp.tracker.ui

import android.content.Context
import android.content.Intent
import android.os.Build
import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kmsp.tracker.net.ReplayApiClient
import com.kmsp.tracker.presence.PresenceEngine
import com.kmsp.tracker.presence.SyncMode
import com.kmsp.tracker.sync.ExactAlarmReceiver
import com.kmsp.tracker.sync.ReplaySyncService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReplayDashboardScreen() {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE) }
    val scope = rememberCoroutineScope()

    var backendUrl by remember { mutableStateOf(prefs.getString("backend_url", "http://192.168.0.213:8080") ?: "http://192.168.0.213:8080") }
    var apiToken by remember { mutableStateOf(prefs.getString("api_token", "") ?: "") }
    var selectedMode by remember { mutableStateOf(PresenceEngine.getMode()) }
    var isSyncing by remember { mutableStateOf(false) }
    var diagnosticResult by remember { mutableStateOf("") }

    val lastSyncTs = prefs.getLong("last_sync_timestamp", 0)
    val lastStatus = prefs.getString("last_status", "Never Synced") ?: "Never Synced"

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
                            PresenceEngine.setMode(SyncMode.HOME_TEST)
                        }
                    )
                    Text("Home Test Mode (24/7 uninhibited)")
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(
                        selected = (selectedMode == SyncMode.OFFICE_PROD),
                        onClick = {
                            selectedMode = SyncMode.OFFICE_PROD
                            PresenceEngine.setMode(SyncMode.OFFICE_PROD)
                        }
                    )
                    Text("Office Mode (Work Hours + Ambient Wi-Fi)")
                }
            }
        }

        // Status Card
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(text = "Sync Telemetry Status", fontWeight = FontWeight.SemiBold)
                Text(text = "Last Sync: $lastSyncStr")
                Text(text = "Last Result: $lastStatus")
                Text(text = "Scheduler: Active (every 30 mins)")
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
                    label = { Text("Backend URL (or Cloudflare Tunnel)") },
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

        // Action Buttons
        Button(
            onClick = {
                isSyncing = true
                val intent = Intent(context, ReplaySyncService::class.java)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
                ExactAlarmReceiver.scheduleNextSync(context)
                Toast.makeText(context, "Immediate sync triggered!", Toast.LENGTH_SHORT).show()
                isSyncing = false
            },
            modifier = Modifier.fillMaxWidth()
        ) {
            Text(text = if (isSyncing) "Syncing..." else "Sync Now (Push to Desk Board)")
        }

        // Diagnostics Card
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(text = "Embedded Diagnostics", fontWeight = FontWeight.SemiBold)
                Button(
                    onClick = {
                        scope.launch {
                            diagnosticResult = "Testing connection to $backendUrl..."
                            withContext(Dispatchers.IO) {
                                val client = ReplayApiClient(backendUrl, apiToken)
                                val result = client.fetchReplayBatch(5)
                                diagnosticResult = if (result.isSuccess) {
                                    val data = result.getOrNull() ?: ""
                                    "SUCCESS! Received ${data.length} bytes.\nSample: ${data.take(120)}..."
                                } else {
                                    "FAILED: ${result.exceptionOrNull()?.message}"
                                }
                            }
                        }
                    },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Test Backend 5G/Wi-Fi Connection")
                }

                if (diagnosticResult.isNotBlank()) {
                    Text(
                        text = diagnosticResult,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 4.dp)
                    )
                }
            }
        }
    }
}
