package com.projectlink.fallguard

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.fallGuardDataStore by preferencesDataStore(name = "fall_guard_settings")

class SettingsStore(private val context: Context) {
    val settings: Flow<AppSettings> = context.fallGuardDataStore.data.map { preferences ->
        AppSettings(
            orinBaseUrl = preferences[ORIN_URL] ?: AppSettings().orinBaseUrl,
            deviceName = preferences[DEVICE_NAME] ?: AppSettings().deviceName,
            sharedToken = preferences[SHARED_TOKEN].orEmpty(),
            simulationEnabled = preferences[SIMULATION_ENABLED] ?: true,
            thresholds = ImuThresholds(
                impactG = preferences[IMPACT_G] ?: 2.5f,
                freeFallG = preferences[FREE_FALL_G] ?: 0.65f,
                orientationChangeDeg = preferences[ORIENTATION_DEG] ?: 55f,
                stillnessSeconds = preferences[STILLNESS_SECONDS] ?: 2f,
            ),
        )
    }

    suspend fun save(settings: AppSettings) {
        context.fallGuardDataStore.edit { preferences ->
            preferences[ORIN_URL] = settings.orinBaseUrl.trim().trimEnd('/')
            preferences[DEVICE_NAME] = settings.deviceName.trim()
            preferences[SHARED_TOKEN] = settings.sharedToken
            preferences[SIMULATION_ENABLED] = settings.simulationEnabled
            preferences[IMPACT_G] = settings.thresholds.impactG
            preferences[FREE_FALL_G] = settings.thresholds.freeFallG
            preferences[ORIENTATION_DEG] = settings.thresholds.orientationChangeDeg
            preferences[STILLNESS_SECONDS] = settings.thresholds.stillnessSeconds
        }
    }

    private companion object {
        val ORIN_URL = stringPreferencesKey("orin_url")
        val DEVICE_NAME = stringPreferencesKey("device_name")
        val SHARED_TOKEN = stringPreferencesKey("shared_token")
        val SIMULATION_ENABLED = booleanPreferencesKey("simulation_enabled")
        val IMPACT_G = floatPreferencesKey("impact_g")
        val FREE_FALL_G = floatPreferencesKey("free_fall_g")
        val ORIENTATION_DEG = floatPreferencesKey("orientation_deg")
        val STILLNESS_SECONDS = floatPreferencesKey("stillness_seconds")
    }
}

