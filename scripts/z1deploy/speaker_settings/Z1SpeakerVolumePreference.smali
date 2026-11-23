.class public final Lcom/android/settings/notification/Z1SpeakerVolumePreference;
.super Landroid/support/v14/preference/SwitchPreference;

.method public constructor <init>(Landroid/content/Context;)V
    .locals 1
    invoke-direct {p0, p1}, Landroid/support/v14/preference/SwitchPreference;-><init>(Landroid/content/Context;)V
    const/4 v0, 0x0
    invoke-virtual {p0, v0}, Landroid/support/v7/preference/Preference;->setPersistent(Z)V
    const/4 v0, 0x2
    invoke-virtual {p0, v0}, Landroid/support/v7/preference/Preference;->setOrder(I)V
    const-string v0, "高音量模式"
    invoke-virtual {p0, v0}, Landroid/support/v7/preference/Preference;->setTitle(Ljava/lang/CharSequence;)V
    const-string v0, "提高内置扬声器音量"
    invoke-virtual {p0, v0}, Landroid/support/v7/preference/Preference;->setSummary(Ljava/lang/CharSequence;)V
    return-void
.end method

.method public callChangeListener(Ljava/lang/Object;)Z
    .locals 6
    check-cast p1, Ljava/lang/Boolean;
    invoke-virtual {p1}, Ljava/lang/Boolean;->booleanValue()Z
    move-result v0
    const-string v1, "persist.sys.z1.spk_high"
    if-eqz v0, :off
    const-string v2, "1"
    const-string v3, "z1_speaker_high_volume=1"
    goto :prepare
    :off
    const-string v2, "0"
    const-string v3, "z1_speaker_high_volume=0"
    :prepare
    :prepare_start
    invoke-virtual {p0}, Landroid/support/v7/preference/Preference;->getContext()Landroid/content/Context;
    move-result-object v4
    const-string v5, "audio"
    invoke-virtual {v4, v5}, Landroid/content/Context;->getSystemService(Ljava/lang/String;)Ljava/lang/Object;
    move-result-object v4
    check-cast v4, Landroid/media/AudioManager;
    if-eqz v4, :reject
    invoke-static {v1}, Landroid/os/SystemProperties;->get(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v5
    invoke-static {v1, v2}, Landroid/os/SystemProperties;->set(Ljava/lang/String;Ljava/lang/String;)V
    :prepare_end
    .catch Ljava/lang/RuntimeException; {:prepare_start .. :prepare_end} :rejected
    :apply_start
    invoke-static {v1}, Landroid/os/SystemProperties;->get(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v0
    invoke-virtual {v2, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-eqz v0, :restore
    invoke-virtual {v4, v3}, Landroid/media/AudioManager;->setParameters(Ljava/lang/String;)V
    :apply_end
    .catch Ljava/lang/RuntimeException; {:apply_start .. :apply_end} :apply_failed
    const/4 v0, 0x1
    return v0
    :apply_failed
    move-exception v0
    :restore
    :restore_start
    invoke-static {v1, v5}, Landroid/os/SystemProperties;->set(Ljava/lang/String;Ljava/lang/String;)V
    const/4 v0, 0x0
    invoke-static {v1, v0}, Landroid/os/SystemProperties;->getBoolean(Ljava/lang/String;Z)Z
    move-result v0
    if-eqz v0, :restore_off
    const-string v3, "z1_speaker_high_volume=1"
    goto :restore_notify
    :restore_off
    const-string v3, "z1_speaker_high_volume=0"
    :restore_notify
    invoke-virtual {v4, v3}, Landroid/media/AudioManager;->setParameters(Ljava/lang/String;)V
    :restore_end
    .catch Ljava/lang/RuntimeException; {:restore_start .. :restore_end} :rejected
    goto :reject
    :rejected
    move-exception v0
    :reject
    const/4 v0, 0x0
    return v0
.end method
