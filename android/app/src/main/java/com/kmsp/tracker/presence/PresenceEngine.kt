package com.kmsp.tracker.presence

import android.annotation.SuppressLint
import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import java.util.Calendar
import java.util.Locale

private const val TAG = "KMSP_PRESENCE"

enum class SyncMode {
    HOME_GEOFENCE,    // Enforce Home Wi-Fi connection / SSID match
    OFFICE_PROD,      // Enforce Office hours (Mon-Fri 07:30-18:00) + Office Wi-Fi beacon
    UNINHIBITED_BENCH // 24/7 uninhibited bench testing (no gating)
}

data class PresenceStatus(
    val isPermitted: Boolean,
    val mode: SyncMode,
    val policyName: String,
    val reason: String,
    val isWifiConnected: Boolean,
    val isCellularConnected: Boolean,
    val currentSsid: String,
    val targetHomeSsid: String,
    val targetOfficeSsid: String = "",
    val localIp: String = "",
    val isWorkHours: Boolean? = null
)

object PresenceEngine {
    private var currentMode: SyncMode = SyncMode.HOME_GEOFENCE

    fun init(context: Context) {
        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        val modeStr = prefs.getString("sync_mode", SyncMode.HOME_GEOFENCE.name)
        currentMode = try {
            when (modeStr) {
                "HOME_TEST" -> SyncMode.HOME_GEOFENCE // Seamless migration from old build
                else -> SyncMode.valueOf(modeStr ?: SyncMode.HOME_GEOFENCE.name)
            }
        } catch (e: Exception) {
            SyncMode.HOME_GEOFENCE
        }
        Log.i(TAG, "PresenceEngine initialized with mode: $currentMode")
    }

    fun setMode(mode: SyncMode, save: Boolean = true, context: Context? = null) {
        currentMode = mode
        if (save && context != null) {
            val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
            prefs.edit().putString("sync_mode", mode.name).apply()
        }
        Log.i(TAG, "PresenceEngine mode changed to: $mode")
    }

    fun getMode(): SyncMode = currentMode

    fun getHomeSsid(context: Context): String {
        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        return prefs.getString("home_wifi_ssid", "")?.trim() ?: ""
    }

    fun setHomeSsid(context: Context, ssid: String) {
        val cleanSsid = WifiHelper.sanitizeSsid(ssid)
        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        prefs.edit().putString("home_wifi_ssid", cleanSsid).apply()
        Log.i(TAG, "Home Wi-Fi SSID set to: '$cleanSsid'")
    }

    fun getOfficeSsid(context: Context): String {
        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        return prefs.getString("office_wifi_ssid", "Office, Guest, Corp, Work")?.trim() ?: "Office, Guest, Corp, Work"
    }

    fun setOfficeSsid(context: Context, ssid: String) {
        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        prefs.edit().putString("office_wifi_ssid", ssid.trim()).apply()
        Log.i(TAG, "Office Wi-Fi SSID / keywords set to: '${ssid.trim()}'")
    }

    @SuppressLint("MissingPermission")
    fun getPresenceStatus(context: Context): PresenceStatus {
        val prefs = context.getSharedPreferences("kmsp_prefs", Context.MODE_PRIVATE)
        val targetHomeSsid = prefs.getString("home_wifi_ssid", "")?.trim() ?: ""
        val targetOfficeConfig = getOfficeSsid(context)
        val isWifi = WifiHelper.isWifiConnected(context)
        val isCellular = WifiHelper.isCellularConnected(context)
        val currentSsid = WifiHelper.getCurrentSsid(context)
        val localIp = WifiHelper.getLocalWifiIpAddress(context)

        return when (currentMode) {
            SyncMode.HOME_GEOFENCE -> {
                if (!isWifi) {
                    val reason = if (isCellular) {
                        "Outside Geofence: Connected to Mobile Cellular Data (LTE/5G). Wi-Fi disconnected."
                    } else {
                        "Outside Geofence: Offline / Wi-Fi disconnected."
                    }
                    PresenceStatus(
                        isPermitted = false,
                        mode = currentMode,
                        policyName = "Home Geofence",
                        reason = reason,
                        isWifiConnected = false,
                        isCellularConnected = isCellular,
                        currentSsid = "",
                        targetHomeSsid = targetHomeSsid,
                        targetOfficeSsid = targetOfficeConfig,
                        localIp = localIp
                    )
                } else if (targetHomeSsid.isNotBlank() && currentSsid.isNotBlank()) {
                    val matches = currentSsid.equals(targetHomeSsid, ignoreCase = true)
                    val reason = if (matches) {
                        "Inside Geofence: Connected to Home Wi-Fi '$currentSsid'"
                    } else {
                        "Outside Geofence: Connected to '$currentSsid' (expected '$targetHomeSsid')"
                    }
                    PresenceStatus(
                        isPermitted = matches,
                        mode = currentMode,
                        policyName = "Home Geofence",
                        reason = reason,
                        isWifiConnected = true,
                        isCellularConnected = false,
                        currentSsid = currentSsid,
                        targetHomeSsid = targetHomeSsid,
                        targetOfficeSsid = targetOfficeConfig,
                        localIp = localIp
                    )
                } else if (targetHomeSsid.isNotBlank() && currentSsid.isBlank()) {
                    // Wi-Fi is connected, but Android has redacted the SSID (e.g. Location Services toggle off)
                    // If local IP is a private LAN address (e.g. 192.168.x.x), allow sync while alerting user
                    val hasLanIp = localIp.isNotBlank() && (localIp.startsWith("192.168.") || localIp.startsWith("10.") || localIp.startsWith("172."))
                    val reason = if (hasLanIp) {
                        "Inside Geofence: Connected to local Wi-Fi LAN ($localIp). Note: Enable Location Services for SSID matching."
                    } else {
                        "Outside Geofence: Wi-Fi connected but no local LAN IP found."
                    }
                    PresenceStatus(
                        isPermitted = hasLanIp,
                        mode = currentMode,
                        policyName = "Home Geofence",
                        reason = reason,
                        isWifiConnected = true,
                        isCellularConnected = false,
                        currentSsid = "",
                        targetHomeSsid = targetHomeSsid,
                        targetOfficeSsid = targetOfficeConfig,
                        localIp = localIp
                    )
                } else {
                    // targetHomeSsid is not set yet
                    val reason = if (currentSsid.isNotBlank()) {
                        "Inside Geofence: Connected to Wi-Fi '$currentSsid' (Tap 'Set Current Wi-Fi as Home' to lock)"
                    } else {
                        "Inside Geofence: Connected to local Wi-Fi ($localIp). Home SSID not configured."
                    }
                    PresenceStatus(
                        isPermitted = true,
                        mode = currentMode,
                        policyName = "Home Geofence",
                        reason = reason,
                        isWifiConnected = true,
                        isCellularConnected = false,
                        currentSsid = currentSsid,
                        targetHomeSsid = "",
                        targetOfficeSsid = targetOfficeConfig,
                        localIp = localIp
                    )
                }
            }

            SyncMode.OFFICE_PROD -> {
                val keywords = targetOfficeConfig.split(",")
                    .map { it.trim() }
                    .filter { it.isNotEmpty() }
                    .ifEmpty { listOf("Office", "Guest", "Corp", "Work") }

                // 1. Time Gating: Mon-Fri between 07:30 and 18:00
                val cal = Calendar.getInstance()
                val dayOfWeek = cal.get(Calendar.DAY_OF_WEEK)
                val isWeekday = dayOfWeek in Calendar.MONDAY..Calendar.FRIDAY
                val hour = cal.get(Calendar.HOUR_OF_DAY)
                val minute = cal.get(Calendar.MINUTE)
                val timeMinutes = hour * 60 + minute
                val isWorkHours = isWeekday && (timeMinutes in (7 * 60 + 30)..(18 * 60))

                if (!isWorkHours) {
                    PresenceStatus(
                        isPermitted = false,
                        mode = currentMode,
                        policyName = "Office Mode",
                        reason = "Outside Work Hours (Mon-Fri 07:30–18:00). Current time: %02d:%02d".format(Locale.US, hour, minute),
                        isWifiConnected = isWifi,
                        isCellularConnected = isCellular,
                        currentSsid = currentSsid,
                        targetHomeSsid = targetHomeSsid,
                        targetOfficeSsid = targetOfficeConfig,
                        localIp = localIp,
                        isWorkHours = false
                    )
                } else {
                    // Check if connected Wi-Fi matches any office keyword or exact SSID
                    var officeDetected = false
                    var matchedSsid = ""

                    for (keyword in keywords) {
                        if (currentSsid.contains(keyword, ignoreCase = true)) {
                            officeDetected = true
                            matchedSsid = currentSsid
                            break
                        }
                    }

                    // Passive ambient beacon scan check if not directly connected to office SSID
                    if (!officeDetected) {
                        try {
                            val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
                            val scanResults = wifiManager?.scanResults ?: emptyList()
                            for (result in scanResults) {
                                val s = result.SSID ?: ""
                                for (keyword in keywords) {
                                    if (s.contains(keyword, ignoreCase = true)) {
                                        officeDetected = true
                                        matchedSsid = s
                                        break
                                    }
                                }
                                if (officeDetected) break
                            }
                        } catch (e: Exception) {
                            Log.w(TAG, "Office Wi-Fi scan: ${e.message}")
                        }
                    }

                    if (officeDetected) {
                        PresenceStatus(
                            isPermitted = true,
                            mode = currentMode,
                            policyName = "Office Mode",
                            reason = "Inside Geofence: Work hours active & office Wi-Fi '$matchedSsid' detected",
                            isWifiConnected = isWifi,
                            isCellularConnected = isCellular,
                            currentSsid = currentSsid,
                            targetHomeSsid = targetHomeSsid,
                            targetOfficeSsid = targetOfficeConfig,
                            localIp = localIp,
                            isWorkHours = true
                        )
                    } else {
                        PresenceStatus(
                            isPermitted = false,
                            mode = currentMode,
                            policyName = "Office Mode",
                            reason = "Outside Geofence: Work hours active, but office Wi-Fi ('$targetOfficeConfig') not detected in the air",
                            isWifiConnected = isWifi,
                            isCellularConnected = isCellular,
                            currentSsid = currentSsid,
                            targetHomeSsid = targetHomeSsid,
                            targetOfficeSsid = targetOfficeConfig,
                            localIp = localIp,
                            isWorkHours = true
                        )
                    }
                }
            }

            SyncMode.UNINHIBITED_BENCH -> {
                PresenceStatus(
                    isPermitted = true,
                    mode = currentMode,
                    policyName = "Bench Mode (Uninhibited)",
                    reason = "Bench Test Mode: 24/7 uninhibited execution active",
                    isWifiConnected = isWifi,
                    isCellularConnected = isCellular,
                    currentSsid = currentSsid,
                    targetHomeSsid = targetHomeSsid,
                    targetOfficeSsid = targetOfficeConfig,
                    localIp = localIp
                )
            }
        }
    }

    fun isSyncPermitted(context: Context): Boolean {
        return getPresenceStatus(context).isPermitted
    }
}
