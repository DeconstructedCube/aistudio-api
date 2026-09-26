package com.aistudio.extractor.data

import android.content.Context
import android.content.SharedPreferences
import android.webkit.CookieManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray

/**
 * 本地多资料凭据管理与持久化核心服务
 */
class ProfileManager(
    private val context: Context,
) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences("aistudio_profiles_v1", Context.MODE_PRIVATE)

    /**
     * 获取全部已创建的 Profile 列表（按创建时间排序）
     */
    fun getProfiles(): List<ProfileItem> {
        val raw = prefs.getString(KEY_PROFILES, null) ?: return emptyList()
        return try {
            val arr = JSONArray(raw)
            val list = mutableListOf<ProfileItem>()
            for (i in 0 until arr.length()) {
                val obj = arr.optJSONObject(i) ?: continue
                list.add(ProfileItem.fromJsonObject(obj))
            }
            list
        } catch (e: Exception) {
            emptyList()
        }
    }

    /**
     * 新增一个独立 Profile
     */
    fun addProfile(
        name: String,
        email: String? = null,
    ): ProfileItem {
        val profiles = getProfiles().toMutableList()
        val newProfile =
            ProfileItem(
                name = name.trim().ifBlank { "资料 ${profiles.size + 1}" },
                email = email?.trim()?.ifBlank { null },
            )
        profiles.add(newProfile)
        saveProfilesInternal(profiles)
        return newProfile
    }

    /**
     * 更新 Profile 元数据（名称、邮箱等）
     */
    fun updateProfile(updated: ProfileItem) {
        val profiles = getProfiles().toMutableList()
        val idx = profiles.indexOfFirst { it.id == updated.id }
        if (idx != -1) {
            profiles[idx] = updated.copy(updatedAt = System.currentTimeMillis())
            saveProfilesInternal(profiles)
        }
    }

    /**
     * 删除指定 Profile
     */
    fun deleteProfile(profileId: String) {
        val profiles = getProfiles().toMutableList()
        profiles.removeAll { it.id == profileId }
        saveProfilesInternal(profiles)
    }

    /**
     * 更新指定 Profile 的 Cookie 凭据与邮箱
     */
    fun saveCookies(
        profileId: String,
        rawCookies: String,
        email: String? = null,
    ) {
        val profiles = getProfiles().toMutableList()
        val idx = profiles.indexOfFirst { it.id == profileId }
        if (idx != -1) {
            val curr = profiles[idx]
            val detectedEmail = email ?: extractEmailFromCookies(rawCookies) ?: curr.email
            profiles[idx] =
                curr.copy(
                    cookies = rawCookies,
                    email = detectedEmail,
                    updatedAt = System.currentTimeMillis(),
                )
            saveProfilesInternal(profiles)
        }
    }

    /**
     * 清空指定 Profile 的 Cookie
     */
    fun clearCookies(profileId: String) {
        val profiles = getProfiles().toMutableList()
        val idx = profiles.indexOfFirst { it.id == profileId }
        if (idx != -1) {
            profiles[idx] =
                profiles[idx].copy(
                    cookies = "",
                    updatedAt = System.currentTimeMillis(),
                )
            saveProfilesInternal(profiles)
        }
    }

    /**
     * 将当前 Profile 的历史 Cookie 注入到系统的 CookieManager 中（挂载前调用）
     */
    suspend fun injectProfileCookiesToWebview(profile: ProfileItem) =
        withContext(Dispatchers.Main) {
            val cookieManager = CookieManager.getInstance()
            cookieManager.setAcceptCookie(true)

            // 先清理当前旧状态
            cookieManager.removeAllCookies(null)
            cookieManager.flush()

            // 逐条注入该 Profile 历史保存的凭据
            if (profile.cookies.isNotBlank()) {
                val pairs = profile.cookies.split("; ")
                for (pair in pairs) {
                    if (pair.contains("=")) {
                        cookieManager.setCookie("https://.google.com", pair)
                        cookieManager.setCookie("https://aistudio.google.com", pair)
                        cookieManager.setCookie("https://accounts.google.com", pair)
                    }
                }
                cookieManager.flush()
            }
        }

    /**
     * 导出所有有效凭据为符合 aistudio-api 规范的 JSON 文本
     */
    fun exportBundleJson(): String {
        val bundle = AccountBundle(accounts = getProfiles())
        return bundle.toJsonString(indent = 2)
    }

    /**
     * 从 JSON 文本导入/合并多个账号资料
     */
    fun importBundleJson(jsonStr: String): Int {
        val imported = AccountBundle.fromJsonString(jsonStr)
        if (imported.isEmpty()) return 0

        val current = getProfiles().toMutableList()
        var addedCount = 0
        for (item in imported) {
            val existingIdx =
                current.indexOfFirst {
                    it.id == item.id || (it.email != null && it.email.equals(item.email, ignoreCase = true))
                }
            if (existingIdx != -1) {
                // 更新现有
                current[existingIdx] =
                    current[existingIdx].copy(
                        cookies = item.cookies,
                        email = item.email ?: current[existingIdx].email,
                        updatedAt = System.currentTimeMillis(),
                    )
            } else {
                current.add(item)
                addedCount++
            }
        }
        saveProfilesInternal(current)
        return imported.size
    }

    private fun saveProfilesInternal(list: List<ProfileItem>) {
        val arr = JSONArray()
        for (item in list) {
            arr.put(item.toJsonObject())
        }
        prefs.edit().putString(KEY_PROFILES, arr.toString()).apply()
    }

    private fun extractEmailFromCookies(rawCookies: String): String? {
        val emailRegex = Regex("""[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+""")
        val match = emailRegex.find(rawCookies)
        return match?.value
    }

    companion object {
        private const val KEY_PROFILES = "profiles_list_v1"
    }
}
