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
    .locals 3
    check-cast p1, Ljava/lang/Boolean;
    invoke-virtual {p1}, Ljava/lang/Boolean;->booleanValue()Z
    move-result v0
    const-string v1, "persist.sys.z1.spk_high"
    if-eqz v0, :off
    const-string v2, "1"
    goto :write
    :off
    const-string v2, "0"
    :write
    :try_start
    invoke-static {v1, v2}, Landroid/os/SystemProperties;->set(Ljava/lang/String;Ljava/lang/String;)V
    invoke-static {v1}, Landroid/os/SystemProperties;->get(Ljava/lang/String;)Ljava/lang/String;
    move-result-object v1
    invoke-virtual {v2, v1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    :try_end
    return v0
    .catch Ljava/lang/RuntimeException; {:try_start .. :try_end} :failed
    :failed
    move-exception v0
    const/4 v0, 0x0
    return v0
.end method
