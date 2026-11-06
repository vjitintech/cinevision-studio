# Keys still needing translation

## Agent C (sign-up, admins, security banner) — all in frontend/src/i18n-strings.ts
- only_owner (frontend/src/i18n-strings.ts) — English text CHANGED, re-translate
- auth_first_owner (frontend/src/i18n-strings.ts)
- auth_invite (frontend/src/i18n-strings.ts)
- auth_invite_hint (frontend/src/i18n-strings.ts)
- auth_closed (frontend/src/i18n-strings.ts)
- pw_rule_length (frontend/src/i18n-strings.ts)
- pw_rule_email (frontend/src/i18n-strings.ts)
- sec_exposed_title (frontend/src/i18n-strings.ts)
- sec_exposed_body (frontend/src/i18n-strings.ts) — keep {host}; keep CINEVISION_HOST / CINEVISION_SIGNUP untranslated
- sec_accounts (frontend/src/i18n-strings.ts)
- sec_signup (frontend/src/i18n-strings.ts) — keep {mode}
- sec_you_are (frontend/src/i18n-strings.ts) — keep {role}
- signup_open (frontend/src/i18n-strings.ts)
- signup_owner_only (frontend/src/i18n-strings.ts)
- signup_closed (frontend/src/i18n-strings.ts)
- role_owner (frontend/src/i18n-strings.ts)
- role_admin (frontend/src/i18n-strings.ts)
- role_member (frontend/src/i18n-strings.ts)
- sec_admin_help (frontend/src/i18n-strings.ts)
- sec_make_admin (frontend/src/i18n-strings.ts)
- sec_remove_admin (frontend/src/i18n-strings.ts)
- sec_new_invite (frontend/src/i18n-strings.ts)
- sec_invite_once (frontend/src/i18n-strings.ts)
- sec_invite_open_note (frontend/src/i18n-strings.ts) — keep CINEVISION_SIGNUP=owner_only untranslated
- sec_invites (frontend/src/i18n-strings.ts)
- invite_unused (frontend/src/i18n-strings.ts)
- invite_used (frontend/src/i18n-strings.ts)
- invite_expired (frontend/src/i18n-strings.ts)
- sec_remove (frontend/src/i18n-strings.ts)

# Strings waiting for translation

English source added; the 15 other languages still need them. One line per key: `key — file`.

- FINDINGS["comp.face_sizes"] — backend/cinevision/director/texts.py
- FINDINGS["snd.stt_missing"] — backend/cinevision/director/texts.py
- FINDINGS["snd.dialogue"] — backend/cinevision/director/texts.py
- FINDINGS["snd.dialogue_buried"] — backend/cinevision/director/texts.py
- FINDINGS["snd.mumbled"] — backend/cinevision/director/texts.py
- FINDINGS["snd.dead_air"] — backend/cinevision/director/texts.py
- FINDINGS["snd.fast_delivery"] — backend/cinevision/director/texts.py
- FINDINGS["snd.slow_delivery"] — backend/cinevision/director/texts.py
- FINDINGS["titles.ocr_missing"] — backend/cinevision/director/texts.py
- FINDINGS["titles.read"] — backend/cinevision/director/texts.py
- FINDINGS["titles.too_short_to_read"] — backend/cinevision/director/texts.py
- FINDINGS["titles.unsafe"] — backend/cinevision/director/texts.py
- FINDINGS["titles.low_contrast"] — backend/cinevision/director/texts.py

# Strings waiting for translation

One line per new English key; the translation agent adds them to every language.

- `is_titles` — backend/cinevision/director/texts.py (ASSEMBLY)
- `is_credits` — backend/cinevision/director/texts.py (ASSEMBLY)
- `user_placed` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_ok` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_empty` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_empty` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_repeat` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_repeat` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_misfit_used` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_misfit_used` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_aspect` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_aspect` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_fps` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_fps` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_time_jump` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_time_jump` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_place_jump` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_place_jump` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_look_jump` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_look_jump` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_sandwich` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_sandwich` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_dup_take` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_dup_take` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_beat_order` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_beat_order` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_beat_missing` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_beat_missing` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_titles_not_first` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_titles_first` — backend/cinevision/director/texts.py (ASSEMBLY)
- `chk_credits_not_last` — backend/cinevision/director/texts.py (ASSEMBLY)
- `fix_credits_last` — backend/cinevision/director/texts.py (ASSEMBLY)
- `asm_drop_hint3` — frontend/src/i18n-strings.ts
- `asm_upload_counts` — frontend/src/i18n-strings.ts
- `asm_retrying` — frontend/src/i18n-strings.ts
- `asm_mine` — frontend/src/i18n-strings.ts
- `asm_mine_intro` — frontend/src/i18n-strings.ts
- `asm_used` — frontend/src/i18n-strings.ts
- `asm_not_used_list` — frontend/src/i18n-strings.ts
- `asm_move_up` — frontend/src/i18n-strings.ts
- `asm_move_down` — frontend/src/i18n-strings.ts
- `asm_leave_out` — frontend/src/i18n-strings.ts
- `asm_use_clip` — frontend/src/i18n-strings.ts
- `asm_moved` — frontend/src/i18n-strings.ts
- `asm_left_out` — frontend/src/i18n-strings.ts
- `asm_put_back` — frontend/src/i18n-strings.ts
- `asm_check` — frontend/src/i18n-strings.ts
- `asm_checked` — frontend/src/i18n-strings.ts
- `asm_reset` — frontend/src/i18n-strings.ts
- `asm_reset_done` — frontend/src/i18n-strings.ts
- `asm_unchecked` — frontend/src/i18n-strings.ts
- `asm_stale` — frontend/src/i18n-strings.ts
- `asm_fix` — frontend/src/i18n-strings.ts
- `asm_mine_runtime` — frontend/src/i18n-strings.ts
- `asm_download` — frontend/src/i18n-strings.ts
- `asm_dl_edl_suggested` — frontend/src/i18n-strings.ts
- `asm_dl_xml_suggested` — frontend/src/i18n-strings.ts
- `asm_dl_edl_mine` — frontend/src/i18n-strings.ts
- `asm_dl_xml_mine` — frontend/src/i18n-strings.ts
- `asm_drop_here` — frontend/src/i18n-strings.ts
- `asm_drag_handle` — frontend/src/i18n-strings.ts
- `asm_dl_hint` — frontend/src/i18n-strings.ts
