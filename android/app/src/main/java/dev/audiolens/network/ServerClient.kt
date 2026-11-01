package dev.audiolens.network

import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class ServerClient(private val baseUrl: String, private val token: String) {
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    fun health(): String {
        val request = Request.Builder()
            .url("$baseUrl/health")
            .header("Authorization", "Bearer $token")
            .build()
        client.newCall(request).execute().use { response ->
            return JSONObject(response.body!!.string()).toString()
        }
    }
}
