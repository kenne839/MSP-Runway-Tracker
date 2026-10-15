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

        // 1. Dispatch directly to ephemeral ForegroundService within 5-second SLA
        val serviceIntent = Intent(context, ReplaySyncService::class.java).apply {
            action = intent.action
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
        fun scheduleNextSync(context: Context, delayMs: Long = SYNC_INTERVAL_MS) {
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
    }
}
