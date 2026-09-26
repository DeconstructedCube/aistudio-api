package com.aistudio.extractor.data

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/**
 * 独立 Google 账号 Profile 模型
 */
data class ProfileItem(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val email: String? = null,
    val cookies: String = "",
    val authUser: String = "0",
    val createdAt: Long = System.currentTimeMillis(),
    val updatedAt: Long = System.currentTimeMillis(),
) {
    val isLoggedIn: Boolean
        get() = cookies.contains("SAPISID") || cookies.contains("SID")

    val cookieCount: Int
        get() {
            if (cookies.isBlank()) return 0
            return cookies.split(";").filter { it.contains("=") }.size
        }

    fun toJsonObject(): JSONObject =
        JSONObject().apply {
            put("id", id)
            put("name", name)
            put("email", email ?: JSONObject.NULL)
            put("cookies", cookies)
            put("auth_user", authUser)
            put("created_at", createdAt)
            put("updated_at", updatedAt)
        }

    companion object {
        fun fromJsonObject(json: JSONObject): ProfileItem =
            ProfileItem(
                id = json.optString("id", UUID.randomUUID().toString()),
                name = json.optString("name", "未命名账号"),
                email = if (json.isNull("email") || json.optString("email").isBlank()) null else json.optString("email"),
                cookies = json.optString("cookies", ""),
                authUser = json.optString("auth_user", "0"),
                createdAt = json.optLong("created_at", System.currentTimeMillis()),
                updatedAt = json.optLong("updated_at", System.currentTimeMillis()),
            )
    }
}

/**
 * 与 aistudio-api 后端完全对齐的凭据 Bundle 包结构
 */
data class AccountBundle(
    val version: Int = 1,
    val exportedAt: String = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).format(Date()),
    val accounts: List<ProfileItem>,
) {
    fun toJsonString(indent: Int = 2): String {
        val root =
            JSONObject().apply {
                put("version", version)
                put("exported_at", exportedAt)
                val arr = JSONArray()
                for (acc in accounts) {
                    // 仅导出存在有效 cookie 的账号
                    if (acc.isLoggedIn) {
                        val item =
                            JSONObject().apply {
                                put("id", acc.id)
                                put("name", acc.name)
                                put("email", acc.email ?: JSONObject.NULL)
                                put("cookies", acc.cookies)
                                put("auth_user", acc.authUser)
                            }
                        arr.put(item)
                    }
                }
                put("accounts", arr)
            }
        return if (indent > 0) root.toString(indent) else root.toString()
    }

    companion object {
        fun fromJsonString(jsonStr: String): List<ProfileItem> {
            val result = mutableListOf<ProfileItem>()
            val parsed =
                if (jsonStr.trim().startsWith("[")) {
                    JSONArray(jsonStr)
                } else {
                    val obj = JSONObject(jsonStr)
                    obj.optJSONArray("accounts") ?: obj.optJSONArray("profiles") ?: JSONArray()
                }
            for (i in 0 until parsed.length()) {
                val item = parsed.optJSONObject(i) ?: continue
                val cookies = item.optString("cookies", "")
                if (cookies.isNotBlank()) {
                    result.add(
                        ProfileItem(
                            id = item.optString("id", UUID.randomUUID().toString()),
                            name = item.optString("name", "导入账号 ${i + 1}"),
                            email = if (item.isNull("email")) null else item.optString("email"),
                            cookies = cookies,
                            authUser = item.optString("auth_user", "0"),
                        ),
                    )
                }
            }
            return result
        }
    }
}
