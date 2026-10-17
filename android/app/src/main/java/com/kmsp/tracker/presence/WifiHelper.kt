package com.kmsp.tracker.presence

import android.annotation.SuppressLint
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.util.Log
import java.util.Locale

object WifiHelper {
    private const val TAG = "KMSP_WIFI"

    /**
     * Checks if the active network connection is Wi-Fi.
     */
    fun isWifiConnected(context: Context): Boolean {
        return try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return false
            val activeNetwork = cm.activeNetwork ?: return false
            val caps = cm.getNetworkCapabilities(activeNetwork) ?: return false
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
        } catch (e: Exception) {
            Log.w(TAG, "Error checking wifi connection: ${e.message}")
            false
        }
    }

    /**
     * Checks if the active network connection is Mobile Cellular Data (LTE/5G).
     */
    fun isCellularConnected(context: Context): Boolean {
        return try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return false
            val activeNetwork = cm.activeNetwork ?: return false
            val caps = cm.getNetworkCapabilities(activeNetwork) ?: return false
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Returns the sanitized SSID of the currently connected Wi-Fi network,
     * or empty string if not on Wi-Fi or if Android has redacted it.
     */
    @SuppressLint("MissingPermission")
    fun getCurrentSsid(context: Context): String {
        try {
            if (!isWifiConnected(context)) {
                return ""
            }

            // Android 10+ (API 29+): Obtain WifiInfo from NetworkCapabilities.transportInfo
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                val activeNetwork = cm?.activeNetwork
                if (activeNetwork != null) {
                    val caps = cm.getNetworkCapabilities(activeNetwork)
                    val transportInfo = caps?.transportInfo
                    if (transportInfo is WifiInfo) {
                        val ssid = sanitizeSsid(transportInfo.ssid)
                        if (ssid.isNotBlank()) {
                            return ssid
                        }
                    }
                }
            }

            // Fallback: WifiManager.connectionInfo
            val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            val wifiInfo = wifiManager?.connectionInfo
            if (wifiInfo != null) {
                val ssid = sanitizeSsid(wifiInfo.ssid)
                if (ssid.isNotBlank()) {
                    return ssid
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error getting current SSID: ${e.message}")
        }
        return ""
    }

    /**
     * Strips enclosing double quotes and filters out Android placeholder SSIDs.
     */
    fun sanitizeSsid(raw: String?): String {
        if (raw == null) return ""
        var s = raw.trim()
        if (s.startsWith("\"") && s.endsWith("\"") && s.length >= 2) {
            s = s.substring(1, s.length - 1)
        }
        if (s.equals("<unknown ssid>", ignoreCase = true) || s == "0x" || s.isEmpty()) {
            return ""
        }
        return s
    }

    /**
     * Retrieves the device's local IPv4 address on the Wi-Fi interface (e.g. 192.168.0.x).
     */
    fun getLocalWifiIpAddress(context: Context): String {
        return try {
            val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager ?: return ""
            val ipInt = wifiManager.connectionInfo?.ipAddress ?: 0
            if (ipInt == 0) return ""
            String.format(
                Locale.US,
                "%d.%d.%d.%d",
                ipInt and 0xff,
                ipInt shr 8 and 0xff,
                ipInt shr 16 and 0xff,
                ipInt shr 24 and 0xff
            )
        } catch (e: Exception) {
            ""
        }
    }
}
