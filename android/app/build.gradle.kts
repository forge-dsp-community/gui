plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// the web GUI: the build of the repository root (npm run build -> ../dist) goes into the assets
val webDist = rootProject.layout.projectDirectory.dir("../dist")
val copyWeb by tasks.registering(Sync::class) {
    from(webDist) { exclude("**/*.gz") }   // the compressed copies are for the ESP32 bridge
    into(layout.buildDirectory.dir("web"))
    doFirst { require(webDist.file("index.html").asFile.exists()) { "web GUI not built: run 'npm run build' in the repository root first" } }
}

android {
    namespace = "io.github.psych0d0g.forgedsp"
    compileSdk = 35
    defaultConfig {
        applicationId = "io.github.psych0d0g.forgedsp"
        minSdk = 24          // head units from Android 7
        targetSdk = 35
        // CI: from the version, ((x*100+y)*100+z)*1000 + commits since the tag (update/Updater.kt)
        versionCode = (project.findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = (project.findProperty("versionName") as String?) ?: "0.1.0-dev"
    }
    // the release key (CI: secret ANDROID_KEYSTORE); updates install only with the same signature
    val ks = System.getenv("ANDROID_KEYSTORE_FILE")
    signingConfigs {
        if (ks != null) create("release") {
            storeFile = file(ks)
            storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
            keyAlias = System.getenv("ANDROID_KEY_ALIAS") ?: "forge-dsp"
            keyPassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
        }
    }
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("web"))
    buildTypes {
        release {
            isMinifyEnabled = false
            // without the release key (forks, local builds): the debug key, installable but no updates over it
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

tasks.named("preBuild") { dependsOn(copyWeb) }

dependencies {
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("com.github.mik3y:usb-serial-for-android:3.8.1")
}
