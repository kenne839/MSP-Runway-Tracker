package com.kmsp.tracker.sync

import android.content.Context
import android.util.Log
import com.kmsp.tracker.ble.BleNusClient
import com.kmsp.tracker.diagnostics.BatteryHelper
import com.kmsp.tracker.diagnostics.DiagnosticsRepository
import com.kmsp.tracker.diagnostics.FlightOpSummary
import com.kmsp.tracker.diagnostics.SyncDiagnosticEntry
import com.kmsp.tracker.net.ReplayApiClient
import com.kmsp.tracker.presence.PresenceEngine
import org.json.JSONArray
import org.json.JSONObject

object ReplaySyncExecutor {
    private const val TAG = "KMSP_EXECUTOR"

    suspend fun executeSync(
        context: Context,
        ignorePresence: Boolean = false,
        triggerSource: String = "BACKGROUND_ALARM",
        wakeLockDurationMs: Long = 0L,
        onProgress: (String) -> Unit = {}
    ): Result<Int> {
        val syncStartTime = System.currentTimeMillis()
        val batteryBefore = BatteryHelper.getBatteryLevel(context)
        val isCharging = BatteryHelper.isCharging(context)

        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        PresenceEngine.init(context)

        val previousSyncTs = prefs.getLong("last_sync_timestamp", 0L)
        val elapsedSinceLastSyncMs = if (previousSyncTs > 0) syncStartTime - previousSyncTs else 0L
        val jitterSec = if (previousSyncTs > 0) (elapsedSinceLastSyncMs - (30 * 60 * 1000L)) / 1000 else 0L
        val isDelayed = if (previousSyncTs > 0) (elapsedSinceLastSyncMs > 35 * 60 * 1000L) else false

        if (!ignorePresence && !PresenceEngine.isSyncPermitted(context)) {
            val msg = "Presence policy inhibited sync execution (Outside office or hours)."
            Log.i(TAG, msg)
            prefs.edit().putString("last_status", "SKIPPED: Out of office / inactive").apply()

            DiagnosticsRepository.recordSync(
                context,
                SyncDiagnosticEntry(
                    timestampMs = syncStartTime,
                    triggerSource = triggerSource,
                    elapsedSinceLastSyncMs = elapsedSinceLastSyncMs,
                    intervalJitterSec = jitterSec,
                    isIntervalDelayed = isDelayed,
                    batteryPctBefore = batteryBefore,
                    batteryPctAfter = BatteryHelper.getBatteryLevel(context),
                    isCharging = isCharging,
                    executionDurationMs = System.currentTimeMillis() - syncStartTime,
                    wakeLockDurationMs = wakeLockDurationMs,
                    isSuccess = false,
                    statusSummary = "SKIPPED: Policy inhibited"
                )
            )
            return Result.failure(IllegalStateException(msg))
        }

        val backendUrl = prefs.getString("backend_url", "http://192.168.0.213:8080") ?: "http://192.168.0.213:8080"
        val token = prefs.getString("api_token", "") ?: ""

        onProgress("Connecting to backend at $backendUrl...")
        Log.i(TAG, "Fetching 30-minute replay batch from $backendUrl (source: $triggerSource)...")
        val httpStartTime = System.currentTimeMillis()
        val apiClient = ReplayApiClient(backendUrl, token)
        val fetchResult = apiClient.fetchReplayBatch(windowMinutes = 30)
        val httpLatencyMs = System.currentTimeMillis() - httpStartTime

        if (fetchResult.isFailure) {
            val err = fetchResult.exceptionOrNull()?.message ?: "Unknown HTTP error"
            Log.e(TAG, "Fetch failed: $err")
            prefs.edit().putString("last_status", "HTTP_ERROR: $err").apply()
            onProgress("FAILED: $err")

            DiagnosticsRepository.recordSync(
                context,
                SyncDiagnosticEntry(
                    timestampMs = syncStartTime,
                    triggerSource = triggerSource,
                    elapsedSinceLastSyncMs = elapsedSinceLastSyncMs,
                    intervalJitterSec = jitterSec,
                    isIntervalDelayed = isDelayed,
                    batteryPctBefore = batteryBefore,
                    batteryPctAfter = BatteryHelper.getBatteryLevel(context),
                    isCharging = isCharging,
                    executionDurationMs = System.currentTimeMillis() - syncStartTime,
                    wakeLockDurationMs = wakeLockDurationMs,
                    httpSuccess = false,
                    httpLatencyMs = httpLatencyMs,
                    httpError = err,
                    isSuccess = false,
                    statusSummary = "FAILED: HTTP fetch error ($err)",
                    errorMessage = err
                )
            )
            return Result.failure(IllegalStateException("HTTP Fetch failed: $err"))
        }

        val jsonPayload = fetchResult.getOrNull() ?: return Result.failure(IllegalStateException("Empty payload received"))
        val byteCount = jsonPayload.toByteArray(Charsets.UTF_8).size

        // Parse flight operations for validation and diagnostic display
        var parsedBatchId = 0L
        var parsedWindowMinutes = 30
        var isChronological = true
        var prevOffset = -1
        val flightList = mutableListOf<FlightOpSummary>()
        val flightLabels = mutableListOf<String>()

        try {
            val root = JSONObject(jsonPayload)
            parsedBatchId = root.optLong("batch_id", 0L)
            parsedWindowMinutes = (root.optInt("window_seconds", 1800)) / 60
            val opsArr = root.optJSONArray("operations") ?: JSONArray()
            val totalOps = opsArr.length()

            for (i in 0 until totalOps) {
                val op = opsArr.getJSONObject(i)
                val offset = op.optInt("offset_sec", 0)
                if (offset < prevOffset) {
                    isChronological = false
                }
                prevOffset = offset

                val runway = op.optString("runway", "")
                val action = op.optString("action", "LANDING")
                val callsign = op.optString("callsign", "Unknown")
                val label = op.optString("flight_label", callsign)
                val aircraft = op.optString("aircraft_type", "")
                val route = op.optString("route", "")

                flightList.add(
                    FlightOpSummary(
                        offsetSec = offset,
                        runway = runway,
                        action = action,
                        callsign = callsign,
                        flightLabel = label,
                        aircraftType = aircraft,
                        route = route
                    )
                )

                if (i < 5) {
                    flightLabels.add("$label ($runway $action)")
                }
            }
            if (totalOps > 5) {
                flightLabels.add("+${totalOps - 5} more")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error inspecting batch payload JSON: ${e.message}")
        }

        val flightSummaryText = flightLabels.joinToString(", ")
        val flightCount = flightList.size

        onProgress("Batch fetched ($byteCount bytes, $flightCount flights). Transmitting over BLE...")
        Log.i(TAG, "Batch $parsedBatchId fetched ($byteCount B, $flightCount flights, chronological: $isChronological). Transmitting over BLE...")

        val bleClient = BleNusClient(context)
        val bleResult = bleClient.transmitBatchPayload(jsonPayload, timeoutSec = 10) { progress ->
            onProgress(progress)
        }

        if (bleResult.isSuccess) {
            val transfer = bleResult.getOrThrow()
            val batteryAfter = BatteryHelper.getBatteryLevel(context)
            val totalDurationMs = System.currentTimeMillis() - syncStartTime

            // Parse firmware ACK fields
            var ackBatchId = 0L
            var ackOps = 0
            var ackHeap = 0L
            try {
                val ackJson = JSONObject(transfer.appAck)
                ackBatchId = ackJson.optLong("batch_id", 0L)
                ackOps = ackJson.optInt("ops", 0)
                ackHeap = ackJson.optLong("free_heap", 0L)
            } catch (e: Exception) {
                Log.w(TAG, "Error parsing firmware ACK response: ${e.message}")
            }

            val isBatchVerified = (ackBatchId == parsedBatchId && parsedBatchId != 0L)
            val isOpsVerified = (ackOps == flightCount)

            Log.i(TAG, "Sync complete! ${transfer.bytesSent} B verified by ${transfer.deviceName} (Batch match: $isBatchVerified, Ops match: $isOpsVerified).")
            prefs.edit()
                .putLong("last_sync_timestamp", System.currentTimeMillis())
                .putInt("last_bytes_sent", transfer.bytesSent)
                .putString("last_device_name", transfer.deviceName)
                .putString("last_device_mac", transfer.deviceAddress)
                .putString("last_ack_response", transfer.appAck)
                .putString("last_status", "SUCCESS: Verified by ${transfer.deviceName} ($flightCount flights, ${transfer.bytesSent} B)")
                .apply()

            val successSummary = "SUCCESS: Confirmed by ${transfer.deviceName}!\nACK: ${transfer.appAck}"
            onProgress(successSummary)

            // Record full diagnostic entry in persistent repository
            DiagnosticsRepository.recordSync(
                context,
                SyncDiagnosticEntry(
                    timestampMs = syncStartTime,
                    triggerSource = triggerSource,
                    elapsedSinceLastSyncMs = elapsedSinceLastSyncMs,
                    intervalJitterSec = jitterSec,
                    isIntervalDelayed = isDelayed,
                    batteryPctBefore = batteryBefore,
                    batteryPctAfter = batteryAfter,
                    isCharging = isCharging,
                    executionDurationMs = totalDurationMs,
                    wakeLockDurationMs = if (wakeLockDurationMs > 0) wakeLockDurationMs else totalDurationMs,
                    httpSuccess = true,
                    httpLatencyMs = httpLatencyMs,
                    batchId = parsedBatchId,
                    windowMinutes = parsedWindowMinutes,
                    wirePayloadBytes = byteCount,
                    flightCount = flightCount,
                    isChronological = isChronological,
                    flightSummaryText = flightSummaryText,
                    flights = flightList,
                    bleSuccess = true,
                    connectionMethod = transfer.connectionMethod,
                    bleConnectDurationMs = transfer.connectDurationMs,
                    bleStreamDurationMs = transfer.streamDurationMs,
                    bleAckDurationMs = transfer.ackLatencyMs,
                    bleMtu = transfer.negotiatedMtu,
                    bleBytesTransferred = transfer.bytesSent,
                    targetMac = transfer.deviceAddress,
                    targetName = transfer.deviceName,
                    ackReceived = true,
                    ackRaw = transfer.appAck,
                    ackBatchId = ackBatchId,
                    ackOpsCount = ackOps,
                    ackFreeHeap = ackHeap,
                    isBatchVerified = isBatchVerified,
                    isOpsVerified = isOpsVerified,
                    isSuccess = true,
                    statusSummary = "SUCCESS: Verified by ${transfer.deviceName} ($flightCount flights, ${transfer.bytesSent} B)"
                )
            )

            return Result.success(transfer.bytesSent)
        } else {
            val err = bleResult.exceptionOrNull()?.message ?: "Unknown BLE error"
            val batteryAfter = BatteryHelper.getBatteryLevel(context)
            val totalDurationMs = System.currentTimeMillis() - syncStartTime

            Log.w(TAG, "BLE transmission failed: $err")
            prefs.edit()
                .putString("last_status", "BLE_ERROR: $err")
                .apply()
            onProgress("FAILED: $err")

            DiagnosticsRepository.recordSync(
                context,
                SyncDiagnosticEntry(
                    timestampMs = syncStartTime,
                    triggerSource = triggerSource,
                    elapsedSinceLastSyncMs = elapsedSinceLastSyncMs,
                    intervalJitterSec = jitterSec,
                    isIntervalDelayed = isDelayed,
                    batteryPctBefore = batteryBefore,
                    batteryPctAfter = batteryAfter,
                    isCharging = isCharging,
                    executionDurationMs = totalDurationMs,
                    wakeLockDurationMs = if (wakeLockDurationMs > 0) wakeLockDurationMs else totalDurationMs,
                    httpSuccess = true,
                    httpLatencyMs = httpLatencyMs,
                    batchId = parsedBatchId,
                    windowMinutes = parsedWindowMinutes,
                    wirePayloadBytes = byteCount,
                    flightCount = flightCount,
                    isChronological = isChronological,
                    flightSummaryText = flightSummaryText,
                    flights = flightList,
                    bleSuccess = false,
                    bleError = err,
                    targetMac = prefs.getString("last_device_mac", "") ?: "",
                    targetName = prefs.getString("last_device_name", "") ?: "",
                    isSuccess = false,
                    statusSummary = "FAILED: BLE transmission error ($err)",
                    errorMessage = err
                )
            )

            return Result.failure(IllegalStateException("BLE Transmission failed: $err"))
        }
    }
}
