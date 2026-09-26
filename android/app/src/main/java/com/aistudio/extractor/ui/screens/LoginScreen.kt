package com.aistudio.extractor.ui.screens

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.os.Build
import android.view.View
import android.view.ViewGroup
import android.webkit.*
import android.widget.EditText
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.aistudio.extractor.data.ProfileItem
import com.aistudio.extractor.data.ProfileManager
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun LoginScreen(
    profile: ProfileItem,
    profileManager: ProfileManager,
    onFinished: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var currentUrl by remember { mutableStateOf("https://aistudio.google.com/prompts/new_chat") }
    var pageTitle by remember { mutableStateOf("") }
    var pageProgress by remember { mutableIntStateOf(0) }
    var isLoading by remember { mutableStateOf(true) }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var urlAnchorRef by remember { mutableStateOf<EditText?>(null) }

    fun teardownWebView() {
        webViewRef?.let { wv ->
            try {
                wv.stopLoading()
                wv.clearHistory()
                (wv.parent as? ViewGroup)?.removeView(wv)
                wv.destroy()
            } catch (e: Exception) {
                // ignore
            }
        }
        webViewRef = null
        urlAnchorRef = null
        // 唤起垃圾回收，尽快释放 150MB+ 网页渲染显存与堆内存
        System.gc()
    }

    fun handleSaveAndExit() {
        val cookieManager = CookieManager.getInstance()
        val rawAiStudioCookies = cookieManager.getCookie("https://aistudio.google.com") ?: ""
        val rawGoogleCookies = cookieManager.getCookie("https://.google.com") ?: ""

        val combinedCookies =
            buildString {
                if (rawAiStudioCookies.isNotBlank()) append(rawAiStudioCookies)
                if (rawGoogleCookies.isNotBlank()) {
                    if (isNotEmpty()) append("; ")
                    append(rawGoogleCookies)
                }
            }

        if (!combinedCookies.contains("SAPISID") && !combinedCookies.contains("SID")) {
            Toast.makeText(context, "尚未检测到有效登录凭据，已退出并保留原样", Toast.LENGTH_SHORT).show()
        } else {
            profileManager.saveCookies(profile.id, combinedCookies)
            Toast.makeText(context, "✅ 成功提取并保存「${profile.name}」的凭据！", Toast.LENGTH_SHORT).show()
        }

        teardownWebView()
        onFinished()
    }

    fun handleCancel() {
        teardownWebView()
        onFinished()
    }

    BackHandler {
        handleCancel()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = profile.name,
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            maxLines = 1,
                        )
                        Text(
                            text = if (isLoading) "正在加载页面..." else (pageTitle.ifBlank { "Google AI Studio 登录" }),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = { handleCancel() }) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "取消返回")
                    }
                },
                actions = {
                    IconButton(onClick = { webViewRef?.reload() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "刷新页面")
                    }
                    Button(
                        onClick = { handleSaveAndExit() },
                        shape = RoundedCornerShape(10.dp),
                        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp),
                        modifier = Modifier.padding(end = 8.dp),
                    ) {
                        Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(modifier = Modifier.width(4.dp))
                        Text("保存并退出", fontWeight = FontWeight.Bold)
                    }
                },
                colors =
                    TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface,
                    ),
            )
        },
    ) { padding ->
        Column(
            modifier =
                Modifier
                    .padding(padding)
                    .fillMaxSize(),
        ) {
            // 加载进度指示器
            if (isLoading && pageProgress in 1..99) {
                LinearProgressIndicator(
                    progress = { pageProgress / 100f },
                    modifier = Modifier.fillMaxWidth().height(3.dp),
                    color = MaterialTheme.colorScheme.primary,
                )
            } else {
                Spacer(modifier = Modifier.height(3.dp))
            }

            // Bitwarden 原生识别锚点地址栏（带标准 semantic 与 accessibility 特征）
            Surface(
                color = MaterialTheme.colorScheme.surfaceContainerHigh,
                modifier =
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 4.dp),
                shape = RoundedCornerShape(10.dp),
            ) {
                Row(
                    modifier =
                        Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 10.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(
                        imageVector = Icons.Default.Lock,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                    Spacer(modifier = Modifier.width(6.dp))

                    // 嵌入真实的 Android 原生 EditText，为 Bitwarden Accessibility Scanner 提供完美的 url_bar 锚点
                    AndroidView(
                        factory = { ctx ->
                            EditText(ctx).apply {
                                id = View.generateViewId()
                                contentDescription = "Address and search bar"
                                tag = "url_bar"
                                isFocusable = false
                                textSize = 11f
                                setSingleLine(true)
                                background = null
                                setPadding(0, 0, 0, 0)
                                setText(currentUrl)
                                urlAnchorRef = this
                            }
                        },
                        update = { et ->
                            if (et.text.toString() != currentUrl) {
                                et.setText(currentUrl)
                            }
                        },
                        modifier = Modifier.weight(1f),
                    )
                }
            }

            // 按需挂载的 WebView 容器
            Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                AndroidView(
                    factory = { ctx ->
                        WebView(ctx).apply {
                            layoutParams =
                                ViewGroup.LayoutParams(
                                    ViewGroup.LayoutParams.MATCH_PARENT,
                                    ViewGroup.LayoutParams.MATCH_PARENT,
                                )

                            // 1. 显式开启 Autofill 框架支持，供 Bitwarden 抓取 webDomain
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                                importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_YES
                            }

                            // 2. 网页环境参数调优
                            settings.apply {
                                javaScriptEnabled = true
                                domStorageEnabled = true
                                databaseEnabled = true
                                setSupportMultipleWindows(true)
                                javaScriptCanOpenWindowsAutomatically = true

                                // 3. 彻底伪装为官方 Chrome 移动版（剥离 ; wv 与 Version/4.0 标识）
                                val rawUa = userAgentString
                                userAgentString =
                                    rawUa
                                        .replace("; wv", "")
                                        .replace(Regex("""Version/\d+\.\d+\s*"""), "")
                            }

                            // 允许第三方 Cookie（Google SSO 跨域登录必需）
                            CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)

                            // 4. 事件监听与状态同步
                            webViewClient =
                                object : WebViewClient() {
                                    override fun onPageStarted(
                                        view: WebView?,
                                        url: String?,
                                        favicon: Bitmap?,
                                    ) {
                                        super.onPageStarted(view, url, favicon)
                                        isLoading = true
                                        url?.let {
                                            currentUrl = it
                                            urlAnchorRef?.setText(it)
                                        }
                                    }

                                    override fun onPageFinished(
                                        view: WebView?,
                                        url: String?,
                                    ) {
                                        super.onPageFinished(view, url)
                                        isLoading = false
                                        url?.let {
                                            currentUrl = it
                                            urlAnchorRef?.setText(it)
                                            // 登录完成并跳转至 aistudio 域时，自动静默同步一次 Cookie
                                            if (it.contains("aistudio.google.com") && !it.contains("accounts.google.com")) {
                                                val c = CookieManager.getInstance().getCookie("https://aistudio.google.com")
                                                if (!c.isNullOrBlank()) {
                                                    profileManager.saveCookies(profile.id, c)
                                                }
                                            }
                                        }
                                    }
                                }

                            webChromeClient =
                                object : WebChromeClient() {
                                    override fun onProgressChanged(
                                        view: WebView?,
                                        newProgress: Int,
                                    ) {
                                        super.onProgressChanged(view, newProgress)
                                        pageProgress = newProgress
                                        if (newProgress >= 100) isLoading = false
                                    }

                                    override fun onReceivedTitle(
                                        view: WebView?,
                                        title: String?,
                                    ) {
                                        super.onReceivedTitle(view, title)
                                        pageTitle = title ?: ""
                                    }
                                }

                            webViewRef = this

                            // 5. 挂载时注入 Profile 历史 Cookie 并启动导航
                            scope.launch {
                                profileManager.injectProfileCookiesToWebview(profile)
                                loadUrl(currentUrl)
                            }
                        }
                    },
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
    }
}
