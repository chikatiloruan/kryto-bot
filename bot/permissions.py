import os

def is_admin(vk_api, peer_id: int, user_id: int) -> bool:
    """
    Простая проверка: если пользователь есть в списке ADMINS (env) — разрешено.
    Иначе — проверяем, является ли он админом беседы (getConversationMembers).
    """
    admin_env = os.getenv("ADMINS", "")
    if admin_env:
        try:
            admins = [int(x.strip()) for x in admin_env.split(",") if x.strip()]
            if int(user_id) in admins:
                return True
        except:
            pass

    try:
        conv = vk_api.messages.getConversationMembers(peer_id=peer_id)
        # conv содержит list of profiles with 'is_admin' flags in items? VK returns items with 'is_chat_admin' or 'member_id' etc.
        for item in conv.get("profiles", []):
            if item.get("id") == int(user_id):
                # not exact admin flag in 'profiles' — better inspect 'items'
                break
        for it in conv.get("items", []):
            if it.get("member_id") == user_id or it.get("member_id") == int(user_id):
                # has "is_admin" or "is_owner" flags in item
                if it.get("is_admin") or it.get("is_owner"):
                    return True
    except Exception:
        pass
    return False

