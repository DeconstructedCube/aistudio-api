# Android WebView and Javascript interface rules
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# Preserve Compose runtime annotations
-keepclassmembers class * {
    @androidx.compose.runtime.Composable <methods>;
}
