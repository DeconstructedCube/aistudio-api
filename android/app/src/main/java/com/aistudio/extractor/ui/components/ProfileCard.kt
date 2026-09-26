package com.aistudio.extractor.ui.components

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aistudio.extractor.data.ProfileItem
import com.aistudio.extractor.ui.theme.DangerRed
import com.aistudio.extractor.ui.theme.SuccessGreen

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProfileCard(
    profile: ProfileItem,
    onLoginClick: () -> Unit,
    onEditClick: () -> Unit,
    onClearCookiesClick: () -> Unit,
    onDeleteClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    ElevatedCard(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors =
            CardDefaults.elevatedCardColors(
                containerColor = MaterialTheme.colorScheme.surface,
            ),
        elevation = CardDefaults.elevatedCardElevation(defaultElevation = 1.dp),
    ) {
        Column(
            modifier =
                Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // 顶栏：名称、邮箱与状态徽标
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = profile.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = profile.email ?: "未绑定邮箱（登录后自动提取）",
                        style = MaterialTheme.typography.bodySmall,
                        color = if (profile.email != null) MaterialTheme.colorScheme.onSurfaceVariant else Color.Gray,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }

                Spacer(modifier = Modifier.width(8.dp))

                // 状态徽标 (Logged in vs Empty)
                Surface(
                    color = if (profile.isLoggedIn) SuccessGreen.copy(alpha = 0.12f) else MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(20.dp),
                    border = if (profile.isLoggedIn) null else CardDefaults.outlinedCardBorder(),
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Icon(
                            imageVector = if (profile.isLoggedIn) Icons.Default.CheckCircle else Icons.Default.AccountCircle,
                            contentDescription = null,
                            tint = if (profile.isLoggedIn) SuccessGreen else Color.Gray,
                            modifier = Modifier.size(14.dp),
                        )
                        Text(
                            text = if (profile.isLoggedIn) "已登录 (${profile.cookieCount})" else "未登录",
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.SemiBold,
                            color = if (profile.isLoggedIn) SuccessGreen else Color.Gray,
                        )
                    }
                }
            }

            // 凭据详情预览（若已登录展示哈希摘要，若未登录提示进入登录）
            if (profile.isLoggedIn) {
                Surface(
                    color = MaterialTheme.colorScheme.surfaceContainerHighest.copy(alpha = 0.5f),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = "AUTH_USER: u/${profile.authUser}",
                            style = MaterialTheme.typography.labelSmall,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.primary,
                            fontSize = 10.sp,
                        )
                        Text(
                            text = "已提取 Google 鉴权凭据",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontSize = 10.sp,
                        )
                    }
                }
            }

            // 底部操作按钮栏
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // 主操作：登录 / 重新登录提取
                Button(
                    onClick = onLoginClick,
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(vertical = 10.dp),
                ) {
                    Icon(
                        imageVector = if (profile.isLoggedIn) Icons.Default.Refresh else Icons.Default.Login,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        text = if (profile.isLoggedIn) "重新登录/更新" else "进入登录",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }

                // 次级操作：编辑
                OutlinedButton(
                    onClick = onEditClick,
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 10.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.Edit,
                        contentDescription = "编辑名称",
                        modifier = Modifier.size(16.dp),
                    )
                }

                // 次级操作：清空 Cookie
                if (profile.isLoggedIn) {
                    OutlinedButton(
                        onClick = onClearCookiesClick,
                        shape = RoundedCornerShape(10.dp),
                        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 10.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.CleaningServices,
                            contentDescription = "清空凭据",
                            modifier = Modifier.size(16.dp),
                            tint = Color.Gray,
                        )
                    }
                }

                // 次级操作：删除该 Profile
                OutlinedButton(
                    onClick = onDeleteClick,
                    shape = RoundedCornerShape(10.dp),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 10.dp),
                ) {
                    Icon(
                        imageVector = Icons.Default.DeleteOutline,
                        contentDescription = "删除资料",
                        modifier = Modifier.size(16.dp),
                        tint = DangerRed,
                    )
                }
            }
        }
    }
}
