
.method public onResume()V
    .locals 4
    invoke-super {p0}, Lcom/android/settings/dashboard/DashboardFragment;->onResume()V
    const-string v0, "persist.sys.z1.spk_high"
    invoke-virtual {p0, v0}, Lcom/android/settings/notification/SoundSettings;->findPreference(Ljava/lang/CharSequence;)Landroid/support/v7/preference/Preference;
    move-result-object v1
    check-cast v1, Lcom/android/settings/notification/Z1SpeakerVolumePreference;
    if-nez v1, :refresh
    invoke-virtual {p0}, Lcom/android/settings/notification/SoundSettings;->getPreferenceScreen()Landroid/support/v7/preference/PreferenceScreen;
    move-result-object v2
    invoke-virtual {v2}, Landroid/support/v7/preference/PreferenceScreen;->getContext()Landroid/content/Context;
    move-result-object v3
    new-instance v1, Lcom/android/settings/notification/Z1SpeakerVolumePreference;
    invoke-direct {v1, v3}, Lcom/android/settings/notification/Z1SpeakerVolumePreference;-><init>(Landroid/content/Context;)V
    invoke-virtual {v1, v0}, Landroid/support/v7/preference/Preference;->setKey(Ljava/lang/String;)V
    invoke-virtual {v2, v1}, Landroid/support/v7/preference/PreferenceScreen;->addPreference(Landroid/support/v7/preference/Preference;)Z
    :refresh
    const/4 v2, 0x0
    invoke-static {v0, v2}, Landroid/os/SystemProperties;->getBoolean(Ljava/lang/String;Z)Z
    move-result v2
    invoke-virtual {v1, v2}, Landroid/support/v7/preference/TwoStatePreference;->setChecked(Z)V
    return-void
.end method
