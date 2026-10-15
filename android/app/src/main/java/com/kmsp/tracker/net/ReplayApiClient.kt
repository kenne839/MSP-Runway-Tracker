package com.kmsp.tracker.net

import android.util.Log
import okhttp3.ConnectionPool
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import java.io.IOException
import java.util.concurrent.TimeUnit

private const val TAG = "KMSP_API"

class ReplayApiClient(
    private val baseUrl: String,
    private val token: String = ""
) {
    // Weak 5G cellular modem optimization:
    // 1. Strict timeouts to avoid hanging sockets.
    // 2. ConnectionPool(0) forces an immediate TCP FIN upon reading response, dropping cellular modem out of RRC high-power state.
    // 3. HTTP/1.1 avoids HTTP/2 multiplex ping teardown overhead on poor cellular connections.
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .writeTimeout(3, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .connectionPool(ConnectionPool(0, 1, TimeUnit.NANOSECONDS))
        .protocols(listOf(Protocol.HTTP_1_1))
        .build()

    fun fetchReplayBatch(windowMinutes: Int = 30): Result<String> {
        val endpoint = "$baseUrl/api/v1/office_replay_batch?window_minutes=$windowMinutes"
        val requestBuilder = Request.Builder()
            .url(endpoint)
            .addHeader("Accept-Encoding", "gzip")
            .addHeader("User-Agent", "KMSP-Office-Bridge/1.0")

        if (token.isNotBlank()) {
            requestBuilder.addHeader("X-KMSP-Token", token)
            requestBuilder.addHeader("Authorization", "Bearer $token")
        }

        val request = requestBuilder.build()
        val startTime = System.currentTimeMillis()

        return try {
            client.newCall(request).execute().use { response ->
                val durationMs = System.currentTimeMillis() - startTime
                if (!response.isSuccessful) {
                    val code = response.code
                    Log.w(TAG, "Replay batch request failed with HTTP $code in ${durationMs}ms")
                    return Result.failure(IOException("HTTP $code: ${response.message}"))
                }

                val bodyString = response.body?.string() ?: ""
                Log.i(TAG, "Replay batch fetched successfully (${bodyString.length} bytes, ${durationMs}ms)")
                Result.success(bodyString)
            }
        } catch (e: Exception) {
            val durationMs = System.currentTimeMillis() - startTime
            Log.e(TAG, "Network error fetching batch after ${durationMs}ms: ${e.message}")
            Result.failure(e)
        }
    }
}
