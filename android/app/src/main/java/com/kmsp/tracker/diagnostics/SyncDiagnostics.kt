package com.kmsp.tracker.diagnostics

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

private const val TAG = "KMSP_DIAGNOSTICS"
private const val DIAGNOSTICS_FILE = "kmsp_sync_diagnostics.json"
private const val PREFS_NAME = "kmsp_diagnostics_prefs"

data class FlightOpSummary(
    val offsetSec: Int,
    val runway: String,
    val action: String,
    val callsign: String,
    val flightLabel: String,
    val aircraftType: String,
    val route: String
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("offset_sec", offsetSec)
        put("runway", runway)
        put("action", action)
        put("callsign", callsign)
        put("flight_label", flightLabel)
        put("aircraft_type", aircraftType)
        put("route", route)
    }

    companion object {
        fun fromJson(obj: JSONObject): FlightOpSummary = FlightOpSummary(
            offsetSec = obj.optInt("offset_sec", 0),
            runway = obj.optString("runway", ""),
            action = obj.optString("action", "LANDING"),
            callsign = obj.optString("callsign", "Unknown"),
            flightLabel = obj.optString("flight_label", "Unknown"),
            aircraftType = obj.optString("aircraft_type", ""),
            route = obj.optString("route", "")
        )
    }
}

data class SyncDiagnosticEntry(
    val id: String = UUID.randomUUID().toString(),
    val timestampMs: Long = System.currentTimeMillis(),
    val formattedTime: String = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(timestampMs)),
    val formattedDate: String = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date(timestampMs)),
    val triggerSource: String = "BACKGROUND_ALARM", // "BACKGROUND_ALARM", "MANUAL_UI", "TILE", "BOOT"
    val elapsedSinceLastSyncMs: Long = 0,
    val intervalJitterSec: Long = 0,
    val isIntervalDelayed: Boolean = false,

    // Power & Timing
    val batteryPctBefore: Int = -1,
    val batteryPctAfter: Int = -1,
    val isCharging: Boolean = false,
    val executionDurationMs: Long = 0,
    val wakeLockDurationMs: Long = 0,

    // Backend Fetch
    val httpSuccess: Boolean = false,
    val httpLatencyMs: Long = 0,
    val httpError: String? = null,
    val batchId: Long = 0,
    val windowMinutes: Int = 30,
    val wirePayloadBytes: Int = 0,
    val flightCount: Int = 0,
    val isChronological: Boolean = true,
    val flightSummaryText: String = "",
    val flights: List<FlightOpSummary> = emptyList(),

    // BLE Transport
    val bleSuccess: Boolean = false,
    val connectionMethod: String = "DIRECT_MAC", // "DIRECT_MAC" or "FILTERED_SCAN"
    val bleConnectDurationMs: Long = 0,
    val bleStreamDurationMs: Long = 0,
    val bleAckDurationMs: Long = 0,
    val bleMtu: Int = 23,
    val bleBytesTransferred: Int = 0,
    val bleError: String? = null,
    val targetMac: String = "",
    val targetName: String = "",

    // Firmware Verification
    val ackReceived: Boolean = false,
    val ackRaw: String = "",
    val ackBatchId: Long = 0,
    val ackOpsCount: Int = 0,
    val ackFreeHeap: Long = 0,
    val isBatchVerified: Boolean = false,
    val isOpsVerified: Boolean = false,

    val isSuccess: Boolean = false,
    val statusSummary: String = "",
    val errorMessage: String? = null
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("timestamp_ms", timestampMs)
        put("formatted_time", formattedTime)
        put("formatted_date", formattedDate)
        put("trigger_source", triggerSource)
        put("elapsed_since_last_sync_ms", elapsedSinceLastSyncMs)
        put("interval_jitter_sec", intervalJitterSec)
        put("is_interval_delayed", isIntervalDelayed)
        put("battery_pct_before", batteryPctBefore)
        put("battery_pct_after", batteryPctAfter)
        put("is_charging", isCharging)
        put("execution_duration_ms", executionDurationMs)
        put("wake_lock_duration_ms", wakeLockDurationMs)
        put("http_success", httpSuccess)
        put("http_latency_ms", httpLatencyMs)
        put("http_error", httpError ?: "")
        put("batch_id", batchId)
        put("window_minutes", windowMinutes)
        put("wire_payload_bytes", wirePayloadBytes)
        put("flight_count", flightCount)
        put("is_chronological", isChronological)
        put("flight_summary_text", flightSummaryText)
        put("ble_success", bleSuccess)
        put("connection_method", connectionMethod)
        put("ble_connect_duration_ms", bleConnectDurationMs)
        put("ble_stream_duration_ms", bleStreamDurationMs)
        put("ble_ack_duration_ms", bleAckDurationMs)
        put("ble_mtu", bleMtu)
        put("ble_bytes_transferred", bleBytesTransferred)
        put("ble_error", bleError ?: "")
        put("target_mac", targetMac)
        put("target_name", targetName)
        put("ack_received", ackReceived)
        put("ack_raw", ackRaw)
        put("ack_batch_id", ackBatchId)
        put("ack_ops_count", ackOpsCount)
        put("ack_free_heap", ackFreeHeap)
        put("is_batch_verified", isBatchVerified)
        put("is_ops_verified", isOpsVerified)
        put("is_success", isSuccess)
        put("status_summary", statusSummary)
        put("error_message", errorMessage ?: "")

        val flightsArr = JSONArray()
        flights.forEach { flightsArr.put(it.toJson()) }
        put("flights", flightsArr)
    }

    companion object {
        fun fromJson(obj: JSONObject): SyncDiagnosticEntry {
            val flightsList = mutableListOf<FlightOpSummary>()
            val arr = obj.optJSONArray("flights")
            if (arr != null) {
                for (i in 0 until arr.length()) {
                    flightsList.add(FlightOpSummary.fromJson(arr.getJSONObject(i)))
                }
            }
            return SyncDiagnosticEntry(
                id = obj.optString("id", UUID.randomUUID().toString()),
                timestampMs = obj.optLong("timestamp_ms", System.currentTimeMillis()),
                formattedTime = obj.optString("formatted_time", ""),
                formattedDate = obj.optString("formatted_date", ""),
                triggerSource = obj.optString("trigger_source", "BACKGROUND_ALARM"),
                elapsedSinceLastSyncMs = obj.optLong("elapsed_since_last_sync_ms", 0),
                intervalJitterSec = obj.optLong("interval_jitter_sec", 0),
                isIntervalDelayed = obj.optBoolean("is_interval_delayed", false),
                batteryPctBefore = obj.optInt("battery_pct_before", -1),
                batteryPctAfter = obj.optInt("battery_pct_after", -1),
                isCharging = obj.optBoolean("is_charging", false),
                executionDurationMs = obj.optLong("execution_duration_ms", 0),
                wakeLockDurationMs = obj.optLong("wake_lock_duration_ms", 0),
                httpSuccess = obj.optBoolean("http_success", false),
                httpLatencyMs = obj.optLong("http_latency_ms", 0),
                httpError = obj.optString("http_error").ifEmpty { null },
                batchId = obj.optLong("batch_id", 0),
                windowMinutes = obj.optInt("window_minutes", 30),
                wirePayloadBytes = obj.optInt("wire_payload_bytes", 0),
                flightCount = obj.optInt("flight_count", 0),
                isChronological = obj.optBoolean("is_chronological", true),
                flightSummaryText = obj.optString("flight_summary_text", ""),
                flights = flightsList,
                bleSuccess = obj.optBoolean("ble_success", false),
                connectionMethod = obj.optString("connection_method", "DIRECT_MAC"),
                bleConnectDurationMs = obj.optLong("ble_connect_duration_ms", 0),
                bleStreamDurationMs = obj.optLong("ble_stream_duration_ms", 0),
                bleAckDurationMs = obj.optLong("ble_ack_duration_ms", 0),
                bleMtu = obj.optInt("ble_mtu", 23),
                bleBytesTransferred = obj.optInt("ble_bytes_transferred", 0),
                bleError = obj.optString("ble_error").ifEmpty { null },
                targetMac = obj.optString("target_mac", ""),
                targetName = obj.optString("target_name", ""),
                ackReceived = obj.optBoolean("ack_received", false),
                ackRaw = obj.optString("ack_raw", ""),
                ackBatchId = obj.optLong("ack_batch_id", 0),
                ackOpsCount = obj.optInt("ack_ops_count", 0),
                ackFreeHeap = obj.optLong("ack_free_heap", 0),
                isBatchVerified = obj.optBoolean("is_batch_verified", false),
                isOpsVerified = obj.optBoolean("is_ops_verified", false),
                isSuccess = obj.optBoolean("is_success", false),
                statusSummary = obj.optString("status_summary", ""),
                errorMessage = obj.optString("error_message").ifEmpty { null }
            )
        }
    }
}

data class DiagnosticSessionSummary(
    val sessionStartTimeMs: Long,
    val sessionDurationFormatted: String,
    val totalAttempts: Int,
    val successfulSyncs: Int,
    val failedSyncs: Int,
    val skippedCount: Int = 0,
    val successRatePct: Int,
    val delayedSyncCount: Int,
    val totalFlightsVerified: Int,
    val totalBytesTransferred: Int,
    val avgExecutionDurationMs: Long,
    val totalWakeLockDurationMs: Long,
    val dutyCyclePct: Double,
    val initialBatteryPct: Int,
    val currentBatteryPct: Int,
    val batteryNetDeltaPct: Int,
    val isCharging: Boolean,
    val lastSyncStatus: String
)

object BatteryHelper {
    fun getBatteryLevel(context: Context): Int {
        return try {
            val ifilter = IntentFilter(Intent.ACTION_BATTERY_CHANGED)
            val batteryStatus: Intent? = context.registerReceiver(null, ifilter)
            val level = batteryStatus?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = batteryStatus?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
            if (level >= 0 && scale > 0) (level * 100 / scale) else -1
        } catch (e: Exception) {
            -1
        }
    }

    fun isCharging(context: Context): Boolean {
        return try {
            val ifilter = IntentFilter(Intent.ACTION_BATTERY_CHANGED)
            val batteryStatus: Intent? = context.registerReceiver(null, ifilter)
            val status = batteryStatus?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
            status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        } catch (e: Exception) {
            false
        }
    }
}

object DiagnosticsRepository {
    private val lock = Any()
    private var listeners = mutableListOf<() -> Unit>()

    fun registerListener(listener: () -> Unit) {
        synchronized(lock) {
            listeners.add(listener)
        }
    }

    fun unregisterListener(listener: () -> Unit) {
        synchronized(lock) {
            listeners.remove(listener)
        }
    }

    private fun notifyListeners() {
        val copy = synchronized(lock) { listeners.toList() }
        copy.forEach { it.invoke() }
    }

    fun recordSync(context: Context, entry: SyncDiagnosticEntry) {
        synchronized(lock) {
            try {
                val entries = loadEntriesInternal(context).toMutableList()
                entries.add(0, entry) // Add newest at beginning
                // Cap log to 100 entries to bound storage
                val capped = if (entries.size > 100) entries.take(100) else entries
                saveEntriesInternal(context, capped)

                // Update session prefs
                val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                val editor = prefs.edit()
                if (!prefs.contains("session_start_ms")) {
                    editor.putLong("session_start_ms", entry.timestampMs)
                    editor.putInt("initial_battery_pct", entry.batteryPctBefore)
                }
                editor.putLong("last_sync_timestamp", entry.timestampMs)
                editor.apply()

                Log.i(TAG, "Recorded sync diagnostic #${entry.id.take(8)}: ${entry.statusSummary}")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to record sync diagnostic: ${e.message}", e)
            }
        }
        notifyListeners()
    }

    fun getEntries(context: Context): List<SyncDiagnosticEntry> {
        synchronized(lock) {
            return loadEntriesInternal(context)
        }
    }

    fun resetSession(context: Context) {
        synchronized(lock) {
            try {
                val file = File(context.filesDir, DIAGNOSTICS_FILE)
                if (file.exists()) {
                    file.delete()
                }
                val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                val currentBattery = BatteryHelper.getBatteryLevel(context)
                prefs.edit()
                    .putLong("session_start_ms", System.currentTimeMillis())
                    .putInt("initial_battery_pct", currentBattery)
                    .remove("last_sync_timestamp")
                    .apply()
                Log.i(TAG, "Diagnostics test session reset.")
            } catch (e: Exception) {
                Log.e(TAG, "Error resetting diagnostics session: ${e.message}", e)
            }
        }
        notifyListeners()
    }

    fun getSessionSummary(context: Context): DiagnosticSessionSummary {
        synchronized(lock) {
            val entries = loadEntriesInternal(context)
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val now = System.currentTimeMillis()

            val sessionStart = prefs.getLong("session_start_ms", if (entries.isNotEmpty()) entries.last().timestampMs else now)
            val initialBattery = prefs.getInt("initial_battery_pct", if (entries.isNotEmpty()) entries.last().batteryPctBefore else BatteryHelper.getBatteryLevel(context))
            val currentBattery = BatteryHelper.getBatteryLevel(context)
            val isCharging = BatteryHelper.isCharging(context)

            val sessionDurationMs = (now - sessionStart).coerceAtLeast(0)
            val hours = sessionDurationMs / (3600 * 1000)
            val mins = (sessionDurationMs % (3600 * 1000)) / (60 * 1000)
            val secs = (sessionDurationMs % (60 * 1000)) / 1000
            val durationFormatted = if (hours > 0) "${hours}h ${mins}m" else "${mins}m ${secs}s"

            val totalAttempts = entries.size
            val successfulSyncs = entries.count { it.isSuccess }
            val skippedCount = entries.count { it.statusSummary.startsWith("SKIPPED") }
            val failedSyncs = totalAttempts - successfulSyncs - skippedCount
            val attemptedSyncs = totalAttempts - skippedCount
            val successRatePct = if (attemptedSyncs > 0) (successfulSyncs * 100 / attemptedSyncs) else if (successfulSyncs > 0) 100 else 100
            val delayedSyncCount = entries.count { it.isIntervalDelayed }

            val totalFlights = entries.filter { it.isSuccess }.sumOf { it.flightCount }
            val totalBytes = entries.filter { it.isSuccess }.sumOf { it.bleBytesTransferred }
            val avgExecMs = if (totalAttempts > 0) entries.map { it.executionDurationMs }.average().toLong() else 0L
            val totalWakeLockMs = entries.sumOf { it.wakeLockDurationMs }

            val dutyCyclePct = if (sessionDurationMs > 0) {
                (totalWakeLockMs.toDouble() / sessionDurationMs.toDouble()) * 100.0
            } else 0.0

            val batteryDelta = if (initialBattery > 0 && currentBattery > 0) (currentBattery - initialBattery) else 0
            val lastStatus = entries.firstOrNull()?.statusSummary ?: "No sync recorded"

            return DiagnosticSessionSummary(
                sessionStartTimeMs = sessionStart,
                sessionDurationFormatted = durationFormatted,
                totalAttempts = totalAttempts,
                successfulSyncs = successfulSyncs,
                failedSyncs = failedSyncs,
                skippedCount = skippedCount,
                successRatePct = successRatePct,
                delayedSyncCount = delayedSyncCount,
                totalFlightsVerified = totalFlights,
                totalBytesTransferred = totalBytes,
                avgExecutionDurationMs = avgExecMs,
                totalWakeLockDurationMs = totalWakeLockMs,
                dutyCyclePct = dutyCyclePct,
                initialBatteryPct = initialBattery,
                currentBatteryPct = currentBattery,
                batteryNetDeltaPct = batteryDelta,
                isCharging = isCharging,
                lastSyncStatus = lastStatus
            )
        }
    }

    fun generateMarkdownReport(context: Context): String {
        val summary = getSessionSummary(context)
        val entries = getEntries(context)

        val sb = StringBuilder()
        sb.appendLine("## 🛫 KMSP Replay Long-Run Test Report")
        sb.appendLine("- **Test Duration**: ${summary.sessionDurationFormatted}")
        sb.appendLine("- **Total Sync Attempts**: ${summary.totalAttempts}")
        sb.appendLine("- **Successful Syncs**: ${summary.successfulSyncs} / ${summary.totalAttempts} (${summary.successRatePct}%)")
        if (summary.skippedCount > 0) {
            sb.appendLine("- **Geofence Inhibited (Away)**: ${summary.skippedCount}")
        }
        sb.appendLine("- **Failed Syncs**: ${summary.failedSyncs}")
        sb.appendLine("- **Delayed / Missed Syncs (>35m)**: ${summary.delayedSyncCount}")
        sb.appendLine("- **Total Flight Operations Verified**: ${summary.totalFlightsVerified}")
        sb.appendLine("- **Total BLE Transferred**: ${summary.totalBytesTransferred} bytes")
        sb.appendLine("- **Average Sync Duration**: ${summary.avgExecutionDurationMs} ms")
        sb.appendLine("- **Total WakeLock Hold**: ${summary.totalWakeLockDurationMs / 1000.0}s (Duty Cycle: ${String.format(Locale.US, "%.3f", summary.dutyCyclePct)}%)")
        sb.appendLine("- **Battery Status**: ${summary.initialBatteryPct}% -> ${summary.currentBatteryPct}% (${if (summary.batteryNetDeltaPct >= 0) "+${summary.batteryNetDeltaPct}%" else "${summary.batteryNetDeltaPct}%"}) [Charging: ${summary.isCharging}]")
        sb.appendLine()
        sb.appendLine("### 📋 Chronological Sync Log")
        sb.appendLine()

        if (entries.isEmpty()) {
            sb.appendLine("_No sync runs recorded yet. Press 'Sync Now' or wait for background alarm._")
        } else {
            entries.forEachIndexed { index, e ->
                val num = entries.size - index
                val statusIcon = if (e.isSuccess) "✅" else "❌"
                sb.appendLine("#### Sync #$num [${e.formattedTime}] $statusIcon ${e.statusSummary}")
                sb.appendLine("- **Trigger**: `${e.triggerSource}` | **Elapsed since prior**: ${if (e.elapsedSinceLastSyncMs > 0) "${e.elapsedSinceLastSyncMs / 60000}m ${(e.elapsedSinceLastSyncMs % 60000) / 1000}s" else "First Sync"} (Jitter: ${e.intervalJitterSec}s)")
                sb.appendLine("- **Power**: Battery: ${e.batteryPctBefore}% -> ${e.batteryPctAfter}% | Duration: ${e.executionDurationMs}ms | WakeLock: ${e.wakeLockDurationMs}ms")
                sb.appendLine("- **Pi Backend**: Batch ID `${e.batchId}` | Flights Captured: ${e.flightCount} | Wire Payload: ${e.wirePayloadBytes} B | Chronological: ${if (e.isChronological) "PASS" else "FAIL"}")
                sb.appendLine("- **BLE Link**: Method: `${e.connectionMethod}` to `${e.targetMac}` | Connect: ${e.bleConnectDurationMs}ms | Stream: ${e.bleStreamDurationMs}ms | MTU: ${e.bleMtu}")
                sb.appendLine("- **Desk Board ACK**: `${e.ackRaw}` | Batch Match: ${if (e.isBatchVerified) "PASS" else "FAIL"} | Ops Match: ${if (e.isOpsVerified) "PASS" else "FAIL"} | Free Heap: ${e.ackFreeHeap} B")
                if (e.flightSummaryText.isNotBlank()) {
                    sb.appendLine("- **Flights**: ${e.flightSummaryText}")
                }
                if (e.errorMessage != null) {
                    sb.appendLine("- **Error Detail**: `${e.errorMessage}`")
                }
                sb.appendLine()
            }
        }
        return sb.toString()
    }

    private fun loadEntriesInternal(context: Context): List<SyncDiagnosticEntry> {
        val file = File(context.filesDir, DIAGNOSTICS_FILE)
        if (!file.exists()) return emptyList()
        return try {
            val jsonStr = file.readText(Charsets.UTF_8)
            val arr = JSONArray(jsonStr)
            val list = mutableListOf<SyncDiagnosticEntry>()
            for (i in 0 until arr.length()) {
                list.add(SyncDiagnosticEntry.fromJson(arr.getJSONObject(i)))
            }
            list
        } catch (e: Exception) {
            Log.e(TAG, "Error reading diagnostics file: ${e.message}")
            emptyList()
        }
    }

    private fun saveEntriesInternal(context: Context, entries: List<SyncDiagnosticEntry>) {
        val file = File(context.filesDir, DIAGNOSTICS_FILE)
        val arr = JSONArray()
        entries.forEach { arr.put(it.toJson()) }
        file.writeText(arr.toString(), Charsets.UTF_8)
    }
}

