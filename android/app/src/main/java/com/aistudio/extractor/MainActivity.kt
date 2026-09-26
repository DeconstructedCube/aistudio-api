package com.aistudio.extractor

import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import com.aistudio.extractor.data.ProfileItem
import com.aistudio.extractor.data.ProfileManager
import com.aistudio.extractor.ui.components.ExportDialog
import com.aistudio.extractor.ui.screens.LoginScreen
import com.aistudio.extractor.ui.screens.MainScreen
import com.aistudio.extractor.ui.theme.AIStudioExtractorTheme

sealed class Screen {
    object Main : Screen()

    data class Login(
        val profile: ProfileItem,
    ) : Screen()
}

class MainActivity : ComponentActivity() {
    private lateinit var profileManager: ProfileManager
    private var pendingExportJson: String = ""

    // SAF 文件保存（导出 JSON）
    private val createDocumentLauncher =
        registerForActivityResult(
            ActivityResultContracts.CreateDocument("application/json"),
        ) { uri: Uri? ->
            if (uri != null && pendingExportJson.isNotBlank()) {
                try {
                    contentResolver.openOutputStream(uri)?.use { os ->
                        os.write(pendingExportJson.toByteArray(Charsets.UTF_8))
                    }
                    Toast.makeText(this, "✅ 凭据文件已成功保存！", Toast.LENGTH_LONG).show()
                } catch (e: Exception) {
                    Toast.makeText(this, "保存文件失败: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }

    // SAF 文件选取（导入备份 JSON）
    private val openDocumentLauncher =
        registerForActivityResult(
            ActivityResultContracts.OpenDocument(),
        ) { uri: Uri? ->
            if (uri != null) {
                try {
                    val jsonStr =
                        contentResolver.openInputStream(uri)?.use { ins ->
                            ins.reader(Charsets.UTF_8).readText()
                        } ?: ""
                    val count = profileManager.importBundleJson(jsonStr)
                    if (count > 0) {
                        Toast.makeText(this, "✅ 成功导入 $count 个账号资料！", Toast.LENGTH_LONG).show()
                    } else {
                        Toast.makeText(this, "未在文件中识别到有效账号凭据", Toast.LENGTH_SHORT).show()
                    }
                } catch (e: Exception) {
                    Toast.makeText(this, "读取备份文件失败: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        profileManager = ProfileManager(this)

        setContent {
            AIStudioExtractorTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    var currentScreen by remember { mutableStateOf<Screen>(Screen.Main) }
                    var profilesState by remember { mutableStateOf(profileManager.getProfiles()) }
                    var exportDialogOpen by remember { mutableStateOf(false) }

                    fun refreshProfiles() {
                        profilesState = profileManager.getProfiles()
                    }

                    when (val screen = currentScreen) {
                        is Screen.Main -> {
                            MainScreen(
                                profiles = profilesState,
                                onOpenLogin = { profile ->
                                    currentScreen = Screen.Login(profile)
                                },
                                onAddProfile = { name, email, authUser ->
                                    profileManager.addProfile(name, email)
                                    refreshProfiles()
                                },
                                onUpdateProfile = { updated ->
                                    profileManager.updateProfile(updated)
                                    refreshProfiles()
                                },
                                onClearCookies = { target ->
                                    profileManager.clearCookies(target.id)
                                    refreshProfiles()
                                    Toast.makeText(this@MainActivity, "已清空「${target.name}」的凭据", Toast.LENGTH_SHORT).show()
                                },
                                onDeleteProfile = { target ->
                                    profileManager.deleteProfile(target.id)
                                    refreshProfiles()
                                    Toast.makeText(this@MainActivity, "已删除「${target.name}」", Toast.LENGTH_SHORT).show()
                                },
                                onExportClick = {
                                    exportDialogOpen = true
                                },
                                onImportBackupClick = {
                                    openDocumentLauncher.launch(arrayOf("application/json", "text/*"))
                                },
                            )

                            if (exportDialogOpen) {
                                val bundleJson =
                                    remember(profilesState) {
                                        profileManager.exportBundleJson()
                                    }
                                val loggedInCount =
                                    remember(profilesState) {
                                        profilesState.count { it.isLoggedIn }
                                    }
                                ExportDialog(
                                    jsonContent = bundleJson,
                                    accountCount = loggedInCount,
                                    onSaveToFileClick = {
                                        pendingExportJson = bundleJson
                                        createDocumentLauncher.launch("aistudio_accounts.json")
                                        exportDialogOpen = false
                                    },
                                    onDismiss = {
                                        exportDialogOpen = false
                                    },
                                )
                            }
                        }

                        is Screen.Login -> {
                            LoginScreen(
                                profile = screen.profile,
                                profileManager = profileManager,
                                onFinished = {
                                    currentScreen = Screen.Main
                                    refreshProfiles()
                                },
                            )
                        }
                    }
                }
            }
        }
    }
}
