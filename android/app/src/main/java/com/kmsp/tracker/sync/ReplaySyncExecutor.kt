package com.kmsp.tracker.sync

import android.content.Context
import android.util.Log
import com.kmsp.tracker.ble.BleNusClient
import com.kmsp.tracker.net.ReplayApiClient
import com.kmsp.tracker.presence.PresenceEngine

object ReplaySyncExecutor {
    private const val TAG = "KMSP_EXECUTOR"

    suspend fun executeSync(
        context: Context,
        ignorePresence: Boolean = false,
        onProgress: (String) -> Unit = {}
    ): Result<Int> {
        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        PresenceEngine.init(context)

        if (!ignorePresence && !PresenceEngine.isSyncPermitted(context)) {
            val msg = "Presence policy inhibited sync execution (Outside office or hours)."
            Log.i(TAG, msg)
            prefs.edit().putString("last_status", "SKIPPED: Out of office / inactive").apply()
            return Result.failure(IllegalStateException(msg))
        }

        val backendUrl = prefs.getString("backend_url", "http://192.168.0.213:8080") ?: "http://192.168.0.213:8080"
        val token = prefs.getString("api_token", "") ?: ""

        onProgress("Connecting to backend at $backendUrl...")
        Log.i(TAG, "Fetching 30-minute replay batch from $backendUrl...")
        val apiClient = ReplayApiClient(backendUrl, token)
        val fetchResult = apiClient.fetchReplayBatch(windowMinutes = 30)

        if (fetchResult.isFailure) {
            val err = fetchResult.exceptionOrNull()?.message ?: "Unknown HTTP error"
            Log.e(TAG, "Fetch failed: $err")
            prefs.edit().putString("last_status", "HTTP_ERROR: $err").apply()
            onProgress("FAILED: $err")
            return Result.failure(IllegalStateException("HTTP Fetch failed: $err"))
        }

        val jsonPayload = fetchResult.getOrNull() ?: return Result.failure(IllegalStateException("Empty payload received"))
        val byteCount = jsonPayload.toByteArray(Charsets.UTF_8).size
        onProgress("Batch fetched ($byteCount bytes). Scanning for ESP32 BLE...")
        Log.i(TAG, "Batch fetched ($byteCount bytes). Transmitting over BLE...")

        val bleClient = BleNusClient(context)
        val bleResult = bleClient.transmitBatchPayload(jsonPayload, timeoutSec = 10) { progress ->
            onProgress(progress)
        }

        if (bleResult.isSuccess) {
            val bytesSent = bleResult.getOrDefault(0)
            Log.i(TAG, "Sync complete! $bytesSent bytes transmitted to ESP32.")
            prefs.edit()
                .putLong("last_sync_timestamp", System.currentTimeMillis())
                .putInt("last_bytes_sent", bytesSent)
                .putString("last_status", "SUCCESS ($bytesSent bytes)")
                .apply()
            onProgress("SUCCESS: Deployed $bytesSent bytes to desk board!")
            return Result.success(bytesSent)
        } else {
            val err = bleResult.exceptionOrNull()?.message ?: "Unknown BLE error"
            Log.w(TAG, "BLE transmission failed: $err")
            prefs.edit()
                .putString("last_status", "BLE_ERROR: $err")
                .apply()
            onProgress("FAILED: $err")
            return Result.failure(IllegalStateException("BLE Transmission failed: $err"))
        }
    }
}

