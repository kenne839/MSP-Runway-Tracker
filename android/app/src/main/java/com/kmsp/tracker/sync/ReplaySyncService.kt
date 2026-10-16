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
import kotlinx.coroutines.runBlocking
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

        // 2. Acquire Partial WakeLock with mandatory 60-second safety timeout (released immediately in finally)
        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "kmsp:sync_worker_lock").apply {
            setReferenceCounted(false)
            acquire(60000L) // 60s max safety ceiling
        }

        Log.i(TAG, "ReplaySyncService started. WakeLock acquired.")

        // 3. Execute sync task off the main thread via shared ReplaySyncExecutor
        executor.execute {
            try {
                runBlocking {
                    ReplaySyncExecutor.executeSync(this@ReplaySyncService, ignorePresence = false)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error in background sync: ${e.message}", e)
            } finally {
                releaseWakeLockAndStop()
            }
        }

        return START_NOT_STICKY
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
