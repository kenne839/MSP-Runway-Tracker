package com.kmsp.tracker.sync

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import com.kmsp.tracker.ble.BleNusClient
import com.kmsp.tracker.net.ReplayApiClient
import com.kmsp.tracker.presence.PresenceEngine
import java.util.concurrent.Executors

private const val TAG = "KMSP_SERVICE"
private const val NOTIFICATION_CHANNEL_ID = "kmsp_sync_channel"
private const val NOTIFICATION_ID = 2001

class ReplaySyncService : Service() {

    private val executor = Executors.newSingleThreadExecutor()
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 1. Post low-priority foreground notification within 5 seconds of launch
        val notification = buildForegroundNotification("Syncing KMSP Telemetry...")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE or ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }

        // 2. Acquire Partial WakeLock with mandatory 15-second hard timeout
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "kmsp:sync_worker_lock").apply {
            setReferenceCounted(false)
            acquire(15000L) // 15s max ceiling
        }

        Log.i(TAG, "ReplaySyncService started. WakeLock acquired.")

        // 3. Execute sync task off the main thread
        executor.execute {
            try {
                executeSyncPipeline()
            } finally {
                releaseWakeLockAndStop()
            }
        }

        return START_NOT_STICKY
    }

    private fun executeSyncPipeline() {
        // Step A: Evaluate Presence Engine (Home Test Mode vs Office Mode)
        val permitted = PresenceEngine.isSyncPermitted(this)
        if (!permitted) {
            Log.i(TAG, "Presence policy inhibited sync execution.")
            return
        }

        // Step B: Load backend connection preferences
        val prefs = getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        val backendUrl = prefs.getString("backend_url", "http://192.168.0.213:8080") ?: "http://192.168.0.213:8080"
        val token = prefs.getString("api_token", "") ?: ""

        // Step C: Fetch 30-minute batch over 5G / Wi-Fi
        Log.i(TAG, "Fetching 30-minute replay batch from $backendUrl...")
        val apiClient = ReplayApiClient(backendUrl, token)
        val fetchResult = apiClient.fetchReplayBatch(windowMinutes = 30)

        if (fetchResult.isFailure) {
            Log.e(TAG, "Fetch failed: ${fetchResult.exceptionOrNull()?.message}")
            return
        }

        val jsonPayload = fetchResult.getOrNull() ?: return
        Log.i(TAG, "Batch fetched (${jsonPayload.length} bytes). Transmitting to ESP32 over BLE NUS...")

        // Step D: Transmit to desk board over BLE Nordic UART Service
        val bleClient = BleNusClient(this)
        val bleResult = bleClient.transmitBatchPayload(jsonPayload, timeoutSec = 8)

        if (bleResult.isSuccess) {
            val bytesSent = bleResult.getOrDefault(0)
            Log.i(TAG, "Replay batch successfully deployed to ESP32 ($bytesSent bytes transmitted)")
            prefs.edit()
                .putLong("last_sync_timestamp", System.currentTimeMillis())
                .putInt("last_bytes_sent", bytesSent)
                .putString("last_status", "SUCCESS")
                .apply()
        } else {
            Log.w(TAG, "BLE transmission failed: ${bleResult.exceptionOrNull()?.message}")
            prefs.edit()
                .putString("last_status", "BLE_ERROR: ${bleResult.exceptionOrNull()?.message}")
                .apply()
        }
    }

    private fun releaseWakeLockAndStop() {
        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
                Log.d(TAG, "WakeLock released.")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error releasing wakeLock: ${e.message}")
        }
        stopForeground(true)
        stopSelf()
        Log.i(TAG, "ReplaySyncService finished and stopped.")
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "KMSP Desk Sync",
                NotificationManager.IMPORTANCE_MIN
            ).apply {
                description = "Background telemetry bridge for KMSP Runway Desk Display"
                setShowBadge(false)
            }
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildForegroundNotification(statusText: String): Notification {
        return NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("KMSP Desk Tracker")
            .setContentText(statusText)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setOngoing(false)
            .build()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
