package com.aistudio.extractor.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.aistudio.extractor.data.ProfileItem
import com.aistudio.extractor.ui.components.ProfileCard
import com.aistudio.extractor.ui.components.ProfileEditDialog

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    profiles: List<ProfileItem>,
    onOpenLogin: (ProfileItem) -> Unit,
    onAddProfile: (name: String, email: String?, authUser: String) -> Unit,
    onUpdateProfile: (ProfileItem) -> Unit,
    onClearCookies: (ProfileItem) -> Unit,
    onDeleteProfile: (ProfileItem) -> Unit,
    onExportClick: () -> Unit,
    onImportBackupClick: () -> Unit,
) {
    var searchQuery by remember { mutableStateOf("") }
    var isCreateDialogOpen by remember { mutableStateOf(false) }
    var editingProfile by remember { mutableStateOf<ProfileItem?>(null) }
    var deletingProfile by remember { mutableStateOf<ProfileItem?>(null) }

    val filteredProfiles =
        remember(profiles, searchQuery) {
            val q = searchQuery.trim().lowercase()
            if (q.isEmpty()) {
                profiles
            } else {
                profiles.filter {
                    it.name.lowercase().contains(q) ||
                        (it.email != null && it.email.lowercase().contains(q))
                }
            }
        }

    val loggedInCount = remember(profiles) { profiles.count { it.isLoggedIn } }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "账号",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                    )
                },
                actions = {
                    IconButton(
                        onClick = onImportBackupClick,
                    ) {
                        Icon(Icons.Default.FileOpen, contentDescription = "导入备份")
                    }
                    IconButton(
                        onClick = onExportClick,
                    ) {
                        Icon(Icons.Default.Share, contentDescription = "导出凭据")
                    }
                },
                colors =
                    TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface,
                    ),
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = { isCreateDialogOpen = true },
                icon = { Icon(Icons.Default.Add, contentDescription = null) },
                text = { Text("添加账号", fontWeight = FontWeight.Bold) },
                shape = RoundedCornerShape(16.dp),
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            )
        },
    ) { padding ->
        Column(
            modifier =
                Modifier
                    .padding(padding)
                    .fillMaxSize()
                    .padding(horizontal = 16.dp),
        ) {
            Spacer(modifier = Modifier.height(8.dp))

            // 搜索过滤栏
            OutlinedTextField(
                value = searchQuery,
                onValueChange = { searchQuery = it },
                placeholder = { Text("搜索资料名称或邮箱...") },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                trailingIcon = {
                    if (searchQuery.isNotEmpty()) {
                        IconButton(onClick = { searchQuery = "" }) {
                            Icon(Icons.Default.Clear, contentDescription = "清空搜索")
                        }
                    }
                },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors =
                    OutlinedTextFieldDefaults.colors(
                        unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh.copy(alpha = 0.5f),
                        focusedContainerColor = MaterialTheme.colorScheme.surface,
                    ),
            )

            Spacer(modifier = Modifier.height(12.dp))

            // 资料卡片列表 (无任何常驻 WebView，内存占用仅十余兆)
            if (filteredProfiles.isEmpty()) {
                Box(
                    modifier =
                        Modifier
                            .fillMaxWidth()
                            .weight(1f),
                    contentAlignment = Alignment.Center,
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.FolderOpen,
                            contentDescription = null,
                            modifier = Modifier.size(48.dp),
                            tint = MaterialTheme.colorScheme.outline,
                        )
                        Text(
                            text = if (searchQuery.isNotBlank()) "未找到匹配的资料" else "暂无资料，点击右下角新建",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.outline,
                        )
                    }
                }
            } else {
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                    contentPadding = PaddingValues(bottom = 80.dp),
                ) {
                    items(filteredProfiles, key = { it.id }) { profile ->
                        ProfileCard(
                            profile = profile,
                            onLoginClick = { onOpenLogin(profile) },
                            onEditClick = { editingProfile = profile },
                            onClearCookiesClick = { onClearCookies(profile) },
                            onDeleteClick = { deletingProfile = profile },
                        )
                    }
                }
            }
        }
    }

    // 新建 Profile 弹窗
    if (isCreateDialogOpen) {
        ProfileEditDialog(
            onDismiss = { isCreateDialogOpen = false },
            onConfirm = { name, email, authUser ->
                onAddProfile(name, email, authUser)
                isCreateDialogOpen = false
            },
        )
    }

    // 编辑 Profile 弹窗
    editingProfile?.let { profile ->
        ProfileEditDialog(
            initialProfile = profile,
            onDismiss = { editingProfile = null },
            onConfirm = { name, email, authUser ->
                onUpdateProfile(profile.copy(name = name, email = email, authUser = authUser))
                editingProfile = null
            },
        )
    }

    // 删除确认弹窗
    deletingProfile?.let { profile ->
        AlertDialog(
            onDismissRequest = { deletingProfile = null },
            title = { Text("删除资料确认") },
            text = { Text("确定要删除资料「${profile.name}」吗？已保存的 Cookie 凭据也将被移除。") },
            confirmButton = {
                Button(
                    onClick = {
                        onDeleteProfile(profile)
                        deletingProfile = null
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                ) {
                    Text("删除")
                }
            },
            dismissButton = {
                TextButton(onClick = { deletingProfile = null }) {
                    Text("取消")
                }
            },
        )
    }
}
