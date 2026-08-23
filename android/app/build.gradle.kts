plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "dev.neuromesh"
    compileSdk = 37

    defaultConfig {
        applicationId = "dev.neuromesh"
        minSdk = 29
        targetSdk = 37
        versionCode = 1
        versionName = "0.1.0"

        // The fleet is four arm64 phones. Shipping four ABIs triples the APK
        // for a sideload that happens over USB on demo day.
        ndk { abiFilters += listOf("arm64-v8a") }

        // One APK, two modes. The orchestrator address is baked in at build
        // time because the demo runs on a phone hotspot with no DNS.
        buildConfigField(
            "String",
            "DEFAULT_ORCHESTRATOR",
            "\"${project.findProperty("neuromesh.orchestrator") ?: "http://192.168.1.100:8000"}\"",
        )
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
        debug {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}")
    }

    androidResources {
        // .tflite is already compressed; letting aapt squash it again breaks
        // the mmap the interpreter needs to load the graph directly.
        noCompress += "tflite"
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.service)

    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)

    implementation(libs.litert)
    implementation(libs.litert.gpu)

    debugImplementation(libs.androidx.ui.tooling)
}
