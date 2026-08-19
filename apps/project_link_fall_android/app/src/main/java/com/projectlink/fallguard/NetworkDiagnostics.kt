package com.projectlink.fallguard

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

object NetworkDiagnostics {
    fun describe(context: Context): String {
        val manager = context.getSystemService(ConnectivityManager::class.java)
        val network = manager.activeNetwork ?: return "无活动网络"
        val capabilities = manager.getNetworkCapabilities(network) ?: return "活动网络信息不可用"
        val transport = when {
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "Wi-Fi"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "以太网"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "VPN"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "蜂窝数据"
            else -> "其他网络"
        }
        val validation = if (capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) {
            "系统已验证"
        } else {
            "系统未验证"
        }
        return "$transport，$validation"
    }
}
