# LiteRT reaches into these from native code and through reflection.
-keep class org.tensorflow.lite.** { *; }
-keep class com.qualcomm.qti.** { *; }
-dontwarn org.tensorflow.lite.gpu.**
-dontwarn com.qualcomm.qti.**

# kotlinx.serialization generates and looks up serializers by name.
-keepattributes *Annotation*, InnerClasses
-keepclassmembers class dev.neuromesh.net.** {
    *** Companion;
}
-keepclasseswithmembers class dev.neuromesh.net.** {
    kotlinx.serialization.KSerializer serializer(...);
}
