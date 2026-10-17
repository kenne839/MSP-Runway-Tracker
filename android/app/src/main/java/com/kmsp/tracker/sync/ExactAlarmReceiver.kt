package com.kmsp.tracker.sync

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

private const val TAG = "KMSP_ALARM"
const val ACTION_TRIGGER_SYNC = "com.kmsp.tracker.ACTION_TRIGGER_SYNC"
const val SYNC_INTERVAL_MS = 30 * 60 * 1000L // 30 minutes

class ExactAlarmReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        Log.i(TAG, "ExactAlarmReceiver triggered with action: ${intent.action}")

        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        val isAutoSyncEnabled = prefs.getBoolean("auto_sync_enabled", true)
        if (!isAutoSyncEnabled) {
            Log.i(TAG, "Auto-sync is disabled by user. Dropping alarm.")
            return
        }

        // If boot completed, schedule next sync without running immediately
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            Log.i(TAG, "Device reboot completed. Rescheduling next sync in 30 minutes.")
            scheduleNextSync(context)
            return
        }

        // 1. Dispatch directly to ephemeral ForegroundService within 5-second SLA
        val serviceIntent = Intent(context, ReplaySyncService::class.java).apply {
            action = intent.action
            putExtra("trigger_source", "BACKGROUND_ALARM")
        }

        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(serviceIntent)
            } else {
                context.startService(serviceIntent)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start ReplaySyncService: ${e.message}")
        }

        // 2. Schedule next exact wake alarm (+30 minutes)
        scheduleNextSync(context)
    }

    companion object {
        fun isAutoSyncEnabled(context: Context): Boolean {
            val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
            return prefs.getBoolean("auto_sync_enabled", true)
        }

        fun getNextSyncTargetMs(context: Context): Long {
            val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
            return prefs.getLong("next_sync_target_ms", 0L)
        }

        fun scheduleNextSync(context: Context, delayMs: Long = SYNC_INTERVAL_MS) {
            val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
            val isAutoSyncEnabled = prefs.getBoolean("auto_sync_enabled", true)
            if (!isAutoSyncEnabled) {
                Log.i(TAG, "Auto-sync is disabled. Not scheduling alarm.")
                return
            }

            val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return
            val intent = Intent(context, ExactAlarmReceiver::class.java).apply {
                action = ACTION_TRIGGER_SYNC
            }
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }
            val pendingIntent = PendingIntent.getBroadcast(context, 1001, intent, flags)

            val triggerAtMillis = System.currentTimeMillis() + delayMs
            prefs.edit().putLong("next_sync_target_ms", triggerAtMillis).apply()

            Log.i(TAG, "Scheduling next exact alarm in ${delayMs / 60000} minutes (at $triggerAtMillis)")

            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, pendingIntent)
                } else {
                    alarmManager.setExact(AlarmManager.RTC_WAKEUP, triggerAtMillis, pendingIntent)
                }
            } catch (e: SecurityException) {
                Log.w(TAG, "SCHEDULE_EXACT_ALARM permission not granted, falling back to setAndAllowWhileIdle: ${e.message}")
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, pendingIntent)
                }
            }
        }

        fun stopAutoSync(context: Context) {
            val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager
            val intent = Intent(context, ExactAlarmReceiver::class.java).apply {
                action = ACTION_TRIGGER_SYNC
            }
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }
            val pendingIntent = PendingIntent.getBroadcast(context, 1001, intent, flags)
            alarmManager?.cancel(pendingIntent)

            val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
            prefs.edit()
                .putBoolean("auto_sync_enabled", false)
                .putLong("next_sync_target_ms", 0L)
                .apply()
            Log.i(TAG, "Auto-sync stopped and pending alarm cancelled.")
        }

        fun startAutoSync(context: Context) {
            val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
            prefs.edit().putBoolean("auto_sync_enabled", true).apply()
            scheduleNextSync(context, SYNC_INTERVAL_MS)
            Log.i(TAG, "Auto-sync started and alarm scheduled.")
        }
    }
}
