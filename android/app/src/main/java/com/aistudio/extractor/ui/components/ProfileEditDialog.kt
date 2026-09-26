package com.aistudio.extractor.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aistudio.extractor.data.ProfileItem

@Composable
fun ProfileEditDialog(
    initialProfile: ProfileItem? = null,
    onDismiss: () -> Unit,
    onConfirm: (name: String, email: String?, authUser: String) -> Unit,
) {
    var name by remember { mutableStateOf(initialProfile?.name ?: "") }
    var email by remember { mutableStateOf(initialProfile?.email ?: "") }
    var authUser by remember { mutableStateOf(initialProfile?.authUser ?: "0") }

    val isEditing = initialProfile != null

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = if (isEditing) "编辑资料设置" else "新建独立资料",
                style = MaterialTheme.typography.titleMedium,
            )
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("资料名称 (如：工作账号)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                )

                OutlinedTextField(
                    value = email,
                    onValueChange = { email = it },
                    label = { Text("绑定邮箱 (选填)") },
                    singleLine = true,
                    placeholder = { Text("登录后系统通常会自动提取") },
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                )

                OutlinedTextField(
                    value = authUser,
                    onValueChange = { authUser = it },
                    label = { Text("AuthUser 索引 (通常为 0)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(12.dp),
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (name.isNotBlank()) {
                        onConfirm(name.trim(), email.trim().ifBlank { null }, authUser.trim().ifBlank { "0" })
                    }
                },
                enabled = name.isNotBlank(),
                shape = RoundedCornerShape(8.dp),
            ) {
                Text(if (isEditing) "保存修改" else "创建资料")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("取消")
            }
        },
        shape = RoundedCornerShape(20.dp),
    )
}
