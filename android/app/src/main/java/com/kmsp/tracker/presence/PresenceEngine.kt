package com.kmsp.tracker.presence

import android.annotation.SuppressLint
import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import java.util.Calendar

private const val TAG = "KMSP_PRESENCE"

enum class SyncMode {
    HOME_TEST,
    OFFICE_PROD
}

interface PresencePolicy {
    fun isSyncPermitted(context: Context): Boolean
    fun getPolicyName(): String
}

class HomeTestPolicy : PresencePolicy {
    override fun isSyncPermitted(context: Context): Boolean {
        // Home test mode is permitted 24/7 for bench verification
        Log.d(TAG, "HomeTestPolicy: Execution permitted unconditionally.")
        return true
    }

    override fun getPolicyName(): String = "Home Test Mode (24/7)"
}

class OfficeProdPolicy(
    private val targetSsidKeywords: List<String> = listOf("Office", "Guest", "Corp", "Work"),
    private val enforceWorkHours: Boolean = true
) : PresencePolicy {

    @SuppressLint("MissingPermission")
    override fun isSyncPermitted(context: Context): Boolean {
        // 1. Time Gating: Mon-Fri between 07:30 and 18:00
        if (enforceWorkHours) {
            val cal = Calendar.getInstance()
            val dayOfWeek = cal.get(Calendar.DAY_OF_WEEK)
            val isWeekday = dayOfWeek in Calendar.MONDAY..Calendar.FRIDAY

            val hour = cal.get(Calendar.HOUR_OF_DAY)
            val minute = cal.get(Calendar.MINUTE)
            val timeMinutes = hour * 60 + minute
            val isWorkHours = timeMinutes in (7 * 60 + 30)..(18 * 60)

            if (!isWeekday || !isWorkHours) {
                Log.i(TAG, "OfficeProdPolicy: Outside work hours (Day: $dayOfWeek, Time: $hour:$minute). Sync suppressed.")
                return false
            }
        }

        // 2. Passive Wi-Fi Near Scan: Check if office ambient SSID is heard in the air
        try {
            val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            if (wifiManager != null) {
                val scanResults = wifiManager.scanResults
                for (result in scanResults) {
                    val ssid = result.SSID ?: ""
                    for (keyword in targetSsidKeywords) {
                        if (ssid.contains(keyword, ignoreCase = true)) {
                            Log.i(TAG, "OfficeProdPolicy: Detected ambient office Wi-Fi beacon '$ssid' (BSSID: ${result.BSSID}). Presence confirmed.")
                            return true
                        }
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Wi-Fi scan failed or throttled: ${e.message}")
        }

        // If Wi-Fi scan is throttled by Android OS (4 scans / 2 min), permit sync as fallback if within work hours
        Log.i(TAG, "OfficeProdPolicy: Permitting execution on work-hours fallback.")
        return true
    }

    override fun getPolicyName(): String = "Office Production Mode (Work Hours + Ambient Wi-Fi)"
}

object PresenceEngine {
    private var currentMode: SyncMode = SyncMode.HOME_TEST
    private var activePolicy: PresencePolicy = HomeTestPolicy()

    fun setMode(mode: SyncMode) {
        currentMode = mode
        activePolicy = when (mode) {
            SyncMode.HOME_TEST -> HomeTestPolicy()
            SyncMode.OFFICE_PROD -> OfficeProdPolicy()
        }
        Log.i(TAG, "PresenceEngine mode changed to: ${activePolicy.getPolicyName()}")
    }

    fun getMode(): SyncMode = currentMode

    fun isSyncPermitted(context: Context): Boolean {
        return activePolicy.isSyncPermitted(context)
    }
}
