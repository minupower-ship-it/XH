import os
import requests
import random
import string
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import stripe
from dotenv import load_dotenv
from mega import Mega

load_dotenv()

app = Flask(__name__)
CORS(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["60 per minute"],
    storage_uri="memory://"
)

# ================== 환경변수 ==================
stripe.api_key            = os.getenv('STRIPE_SECRET_KEY')
WEBHOOK_SECRET            = os.getenv('STRIPE_WEBHOOK_SECRET')

# 서버 1 (xhouse.vip)
DISCORD_BOT_TOKEN         = os.getenv('DISCORD_BOT_TOKEN')
XHOUSE_ROLE_ID            = os.getenv('XHOUSE_ROLE_ID')
XHOUSE_GUILD_ID           = os.getenv('XHOUSE_GUILD_ID')
XHOUSE_TX_CHANNEL_ID      = int(os.getenv('XHOUSE_TX_CHANNEL_ID', '1486794773263024309'))
INVITE_CHANNEL_ID         = os.getenv('DISCORD_INVITE_CHANNEL_ID')
LIFETIME_PRICE_ID         = os.getenv('STRIPE_LIFETIME_PRICE_ID')
VIP_PRICE_ID              = os.getenv('STRIPE_VIP_PRICE_ID')
SUCCESS_URL_S1            = "https://xhouse.vip/success.html?session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL_S1             = "https://xhouse.vip"

# 서버 2 (PBank)
S2_BOT_TOKEN              = os.getenv('S2_DISCORD_BOT_TOKEN')
S2_GUILD_ID               = os.getenv('S2_GUILD_ID')
S2_ROLE_ID                = os.getenv('S2_ROLE_ID')
S2_WEBHOOK_SECRET         = os.getenv('S2_STRIPE_WEBHOOK_SECRET')
S2_PRICE_ID               = os.getenv('S2_STRIPE_PRICE_ID')
PBANK_TX_CHANNEL_ID       = int(os.getenv('PBANK_TX_CHANNEL_ID', '1488024169537867806'))
SUCCESS_URL_S2            = "https://xhouse.vip/success.html?session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL_S2             = "https://xhouse.vip"

# Dummy 채널 (링크 저장 + 코드 저장 + 결제 기록)
CODE_STORE_CHANNEL        = 1488022953525248141

DISCORD_API = "https://discord.com/api/v10"
API_SECRET_KEY = os.getenv('API_SECRET_KEY')

# ================== API Key 인증 ==================
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key')
        if not key or key != API_SECRET_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ================== Discord 헬퍼 ==================
def discord_headers(token):
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

def send_to_channel(token: str, channel_id: int, content: str) -> bool:
    """Discord 채널에 메시지 전송"""
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    res = requests.post(url, headers=discord_headers(token), json={"content": content})
    if res.status_code != 200:
        print(f"[Discord] 전송 실패 (채널 {channel_id}): {res.status_code}")
    return res.status_code == 200

def create_discord_invite():
    url = f"{DISCORD_API}/channels/{INVITE_CHANNEL_ID}/invites"
    res = requests.post(url, headers=discord_headers(DISCORD_BOT_TOKEN), json={
        "max_uses": 1,
        "max_age": 0,
        "unique": True
    })
    res.raise_for_status()
    return f"https://discord.gg/{res.json()['code']}"

def assign_role(discord_id: str):
    """서버 2 유저에게 역할 부여"""
    url = f"{DISCORD_API}/guilds/{S2_GUILD_ID}/members/{discord_id}/roles/{S2_ROLE_ID}"
    res = requests.put(url, headers=discord_headers(S2_BOT_TOKEN))
    return res.status_code in (200, 204)

def send_dm(token: str, discord_id: str, message: str):
    """유저에게 DM 발송"""
    res = requests.post(
        f"{DISCORD_API}/users/@me/channels",
        headers=discord_headers(token),
        json={"recipient_id": discord_id}
    )
    if res.status_code != 200:
        return False
    channel_id = res.json()["id"]
    res2 = requests.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        headers=discord_headers(token),
        json={"content": message}
    )
    return res2.status_code == 200

def get_messages_from_channel(token, channel_id, limit=500):
    """최신 메시지부터 역순으로 조회"""
    url = f"{DISCORD_API}/channels/{channel_id}/messages?limit=100"
    headers = discord_headers(token)
    messages = []
    last_id = None
    while len(messages) < limit:
        url_paged = url + (f"&before={last_id}" if last_id else "")
        res = requests.get(url_paged, headers=headers)
        if res.status_code != 200:
            break
        batch = res.json()
        if not batch:
            break
        messages.extend(batch)
        last_id = batch[-1]['id']
        if len(batch) < 100:
            break
    return messages

# ================== 결제 기록 헬퍼 (Discord 채널 저장) ==================
def find_s1_payment(session_id):
    """S1 결제 기록 조회 → (message_id, invite_url) or None"""
    messages = get_messages_from_channel(DISCORD_BOT_TOKEN, CODE_STORE_CHANNEL)
    for msg in messages:
        content = msg.get('content', '')
        if content.startswith(f"S1_PAY | {session_id} |"):
            parts = content.split(' | ')
            invite_url = parts[2].strip() if len(parts) >= 3 else "NONE"
            return (msg['id'], invite_url if invite_url != "NONE" else None)
    return None

def save_s1_payment(session_id, invite_url=None):
    """S1 결제 기록 저장"""
    invite = invite_url or "NONE"
    content = f"S1_PAY | {session_id} | {invite}"
    send_to_channel(DISCORD_BOT_TOKEN, CODE_STORE_CHANNEL, content)

def update_s1_invite(message_id, session_id, invite_url):
    """S1 결제 기록에 초대링크 업데이트"""
    content = f"S1_PAY | {session_id} | {invite_url}"
    url = f"{DISCORD_API}/channels/{CODE_STORE_CHANNEL}/messages/{message_id}"
    requests.patch(url, headers=discord_headers(DISCORD_BOT_TOKEN), json={"content": content})

def find_s2_payment(session_id):
    """S2 결제 기록 조회 → True if exists, False if not"""
    messages = get_messages_from_channel(DISCORD_BOT_TOKEN, CODE_STORE_CHANNEL)
    for msg in messages:
        content = msg.get('content', '')
        if content.startswith(f"S2_PAY | {session_id} |"):
            return True
    return False

def save_s2_payment(session_id, discord_id, role_granted=0):
    """S2 결제 기록 저장"""
    content = f"S2_PAY | {session_id} | {discord_id} | {role_granted}"
    send_to_channel(DISCORD_BOT_TOKEN, CODE_STORE_CHANNEL, content)

# ================== 서버 1: Checkout Session 생성 ==================
@app.route('/create-checkout', methods=['POST'])
@require_api_key
@limiter.limit("5 per minute")
def create_checkout():
    data       = request.get_json()
    plan       = data.get('plan')
    discord_id = data.get('discord_id')
    price_id   = VIP_PRICE_ID if plan == 'vip' else LIFETIME_PRICE_ID

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='payment',
            success_url=SUCCESS_URL_S1,
            cancel_url=CANCEL_URL_S1,
            metadata={"discord_id": discord_id} if discord_id else {}
        )
        return jsonify({"url": session.url})
    except Exception as e:
        print(f"[S1 Checkout] 생성 실패: {e}")
        return jsonify({"error": str(e)}), 500

# ================== 서버 1: Webhook ==================
@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload    = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception as e:
        print(f"[S1 Webhook] 서명 검증 실패: {e}")
        return jsonify(success=False), 400

    if event['type'] == 'checkout.session.completed':
        session_obj = event['data']['object']
        session_id  = session_obj['id']
        discord_id  = session_obj.get('metadata', {}).get('discord_id')

        existing = find_s1_payment(session_id)
        if not existing:
            save_s1_payment(session_id)
            print(f"[S1 Webhook] 결제 저장: {session_id}")

            if discord_id and XHOUSE_ROLE_ID and XHOUSE_GUILD_ID:
                url = f"{DISCORD_API}/guilds/{XHOUSE_GUILD_ID}/members/{discord_id}/roles/{XHOUSE_ROLE_ID}"
                res = requests.put(url, headers=discord_headers(DISCORD_BOT_TOKEN))
                timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
                if res.status_code in (200, 204):
                    send_dm(DISCORD_BOT_TOKEN, discord_id, "✅ Payment confirmed! Your membership role has been granted. Welcome to X-House! 🎉\n\n⭐ Enjoying your access? Drop a quick review in the server — it means a lot to us!")
                    print(f"[S1 Webhook] 역할 부여 완료: {discord_id}")
                    log_msg = f"✅ `{session_id}` | <@{discord_id}> | Role: ✅ Granted | {timestamp} UTC"
                    send_to_channel(DISCORD_BOT_TOKEN, XHOUSE_TX_CHANNEL_ID, log_msg)
                else:
                    print(f"[S1 Webhook] 역할 부여 실패: {res.status_code}")
                    log_msg = f"⚠️ `{session_id}` | <@{discord_id}> | Role: ❌ FAILED (HTTP {res.status_code}) | {timestamp} UTC"
                    send_to_channel(DISCORD_BOT_TOKEN, XHOUSE_TX_CHANNEL_ID, log_msg)

    return jsonify(success=True), 200

# ================== 서버 1: 초대링크 발급 ==================
@app.route('/create-invite', methods=['POST'])
@limiter.limit("3 per minute")
def create_invite():
    data       = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"error": "session_id is required."}), 400

    existing = find_s1_payment(session_id)

    if not existing:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status != 'paid':
                return jsonify({"error": "Payment not completed."}), 400
            save_s1_payment(session_id)
            existing = (None, None)
        except Exception as e:
            return jsonify({"error": f"Payment verification failed: {str(e)}"}), 400

    message_id, invite_url = existing if existing and len(existing) == 2 else (None, None)

    if invite_url:
        return jsonify({"success": True, "invite_url": invite_url})

    try:
        invite_url = create_discord_invite()
        if message_id:
            update_s1_invite(message_id, session_id, invite_url)
        else:
            save_s1_payment(session_id, invite_url)
        return jsonify({"success": True, "invite_url": invite_url})
    except Exception as e:
        print(f"[S1 Invite] 생성 실패: {e}")
        return jsonify({"error": "Failed to create invite link. Please try again."}), 500

# ================== 서버 2: Checkout Session 생성 ==================
@app.route('/s2/create-checkout', methods=['POST'])
@require_api_key
@limiter.limit("5 per minute")
def s2_create_checkout():
    data       = request.get_json()
    discord_id = data.get('discord_id')

    if not discord_id:
        return jsonify({"error": "discord_id is required."}), 400

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': S2_PRICE_ID, 'quantity': 1}],
            mode='payment',
            success_url=SUCCESS_URL_S2,
            cancel_url=CANCEL_URL_S2,
            metadata={"discord_id": discord_id}
        )
        return jsonify({"url": session.url})
    except Exception as e:
        print(f"[S2 Checkout] 생성 실패: {e}")
        return jsonify({"error": str(e)}), 500

# ================== 서버 2: Webhook (역할 자동 부여) ==================
@app.route('/s2/webhook', methods=['POST'])
def s2_stripe_webhook():
    payload    = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, S2_WEBHOOK_SECRET)
    except Exception as e:
        print(f"[S2 Webhook] 서명 검증 실패: {e}")
        return jsonify(success=False), 400

    if event['type'] == 'checkout.session.completed':
        session    = event['data']['object']
        session_id = session['id']
        discord_id = session.get('metadata', {}).get('discord_id')

        if not discord_id:
            print(f"[S2 Webhook] discord_id 없음: {session_id}")
            return jsonify(success=True), 200

        if not find_s2_payment(session_id):
            success = assign_role(discord_id)
            role_granted = 1 if success else 0
            save_s2_payment(session_id, discord_id, role_granted)
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M')

            if success:
                send_dm(S2_BOT_TOKEN, discord_id, "✅ Payment confirmed! Your membership role has been granted. 🎉\n\n⭐ Enjoying your access? Drop a quick review in the server — it means a lot to us!")
                print(f"[S2 Webhook] 역할 부여 완료: {discord_id}")
                log_msg = f"✅ `{session_id}` | <@{discord_id}> | Role: ✅ Granted | {timestamp} UTC"
                send_to_channel(S2_BOT_TOKEN, PBANK_TX_CHANNEL_ID, log_msg)
            else:
                print(f"[S2 Webhook] 역할 부여 실패: {discord_id}")
                log_msg = f"⚠️ `{session_id}` | <@{discord_id}> | Role: ❌ FAILED | {timestamp} UTC"
                send_to_channel(S2_BOT_TOKEN, PBANK_TX_CHANNEL_ID, log_msg)

    return jsonify(success=True), 200

# ================== 코드 발급/검증 (텔레그램 봇용 - 보류) ==================
def generate_code():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=10))

@app.route('/issue-code', methods=['POST'])
@require_api_key
def issue_code():
    data       = request.get_json()
    discord_id = data.get('discord_id')
    if not discord_id:
        return jsonify({"error": "discord_id required"}), 400

    messages = get_messages_from_channel(DISCORD_BOT_TOKEN, CODE_STORE_CHANNEL)

    for msg in messages:
        content = msg.get('content', '')
        if content.startswith(f"CODE | {discord_id} |"):
            parts = content.split(' | ')
            if len(parts) >= 4:
                existing_code = parts[2]
                used          = parts[3].strip()
                if used == 'used':
                    return jsonify({"error": "Your code has already been used."}), 400
                return jsonify({"success": True, "code": existing_code, "existing": True})

    new_code = generate_code()
    url      = f"{DISCORD_API}/channels/{CODE_STORE_CHANNEL}/messages"
    res      = requests.post(url, headers=discord_headers(DISCORD_BOT_TOKEN), json={
        "content": f"CODE | {discord_id} | {new_code} | unused"
    })
    if res.status_code != 200:
        return jsonify({"error": "Failed to store code"}), 500

    return jsonify({"success": True, "code": new_code, "existing": False})

@app.route('/verify-code', methods=['POST'])
@require_api_key
def verify_code():
    data = request.get_json()
    code = data.get('code', '').strip()
    if not code:
        return jsonify({"error": "code required"}), 400

    messages = get_messages_from_channel(DISCORD_BOT_TOKEN, CODE_STORE_CHANNEL)

    for msg in messages:
        content = msg.get('content', '')
        if not content.startswith("CODE |"):
            continue
        parts = content.split(' | ')
        if len(parts) < 4:
            continue
        discord_id  = parts[1].strip()
        stored_code = parts[2].strip()
        used        = parts[3].strip()

        if stored_code == code:
            if used == 'used':
                return jsonify({"error": "This code has already been used."}), 400
            msg_id  = msg['id']
            new_msg = f"CODE | {discord_id} | {stored_code} | used"
            requests.patch(
                f"{DISCORD_API}/channels/{CODE_STORE_CHANNEL}/messages/{msg_id}",
                headers=discord_headers(DISCORD_BOT_TOKEN),
                json={"content": new_msg}
            )
            return jsonify({"success": True, "discord_id": discord_id})

    return jsonify({"error": "Invalid code."}), 404

# ================== 포스트 조회 (텔레그램 봇용 - 보류) ==================
@app.route('/get-posts', methods=['POST'])
@require_api_key
def get_posts():
    data     = request.get_json()
    category = data.get('category', '').lower()

    CATEGORY_CHANNELS = {
        "asian":    1487319260228358174,
        "hispanic": 1487319298681864342,
        "white":    1487319326204625047,
        "black":    1487319363265626173,
    }

    channel_id = CATEGORY_CHANNELS.get(category)
    if not channel_id:
        return jsonify({"error": "Invalid category"}), 400

    url = f"{DISCORD_API}/channels/{channel_id}/threads/archived/public?limit=50"
    res = requests.get(url, headers=discord_headers(DISCORD_BOT_TOKEN))

    posts = []
    if res.status_code == 200:
        for thread in res.json().get('threads', []):
            posts.append({"id": thread['id'], "name": thread['name']})

    url2 = f"{DISCORD_API}/guilds/{XHOUSE_GUILD_ID}/threads/active"
    res2 = requests.get(url2, headers=discord_headers(DISCORD_BOT_TOKEN))
    if res2.status_code == 200:
        for thread in res2.json().get('threads', []):
            if str(thread.get('parent_id')) == str(channel_id):
                if not any(p['id'] == thread['id'] for p in posts):
                    posts.append({"id": thread['id'], "name": thread['name']})

    return jsonify({"posts": posts})

@app.route('/get-post-link', methods=['POST'])
@require_api_key
def get_post_link():
    data      = request.get_json()
    thread_id = data.get('thread_id')
    if not thread_id:
        return jsonify({"error": "thread_id required"}), 400

    messages = get_messages_from_channel(DISCORD_BOT_TOKEN, CODE_STORE_CHANNEL)

    for msg in messages:
        content = msg.get('content', '')
        if content.startswith(f"{thread_id} |"):
            parts = content.split(' | ', 1)
            if len(parts) == 2:
                link = parts[1].strip()
                key  = link.split('#')[-1] if '#' in link else ""
                return jsonify({"success": True, "link": link, "key": key})

    return jsonify({"error": "Link not found"}), 404

# ================== Mega 자동 스캔 ==================
@app.route('/mega/scan', methods=['POST'])
@require_api_key
def mega_scan():
    mega_email    = os.getenv('MEGA_EMAIL')
    mega_password = os.getenv('MEGA_PASSWORD')

    if not mega_email or not mega_password:
        return jsonify({"error": "Mega credentials not set"}), 500

    data         = request.get_json()
    folder_names = data.get('folders', [])

    if not folder_names:
        return jsonify({"error": "No folders provided"}), 400

    try:
        m     = Mega()
        mega  = m.login(mega_email, mega_password)
        files = mega.get_files()
    except Exception as e:
        print(f"[Mega] 로그인 실패: {e}")
        return jsonify({"error": f"Mega login failed: {str(e)}"}), 500

    # get_files() 결과에서 폴더명 → node 매핑 직접 생성 (mega.find() 대체)
    folder_map = {}
    for node_id, node in files.items():
        if node.get('t') == 1 and isinstance(node.get('a'), dict):
            name = node['a'].get('n')
            if name:
                folder_map[name] = node

    results = []

    for folder_name in folder_names:
        try:
            folder_node = folder_map.get(folder_name)
            if not folder_node:
                results.append({"name": folder_name, "success": False, "reason": "Folder not found in Mega"})
                continue

            link = mega.get_link(folder_node)

            if not link:
                results.append({"name": folder_name, "success": False, "reason": "Failed to get folder link"})
                continue

            key = link.split('#')[-1] if '#' in link else ""

            total_bytes = 0
            folder_h = folder_node.get('h')
            for f in files.values():
                if f.get('t') == 0 and f.get('p') == folder_h:
                    total_bytes += f.get('s', 0)

            file_size = f"{round(total_bytes / (1024 ** 3), 2)}GB" if total_bytes > 0 else "Unknown"

            results.append({
                "name":      folder_name,
                "success":   True,
                "link":      link,
                "key":       key,
                "file_size": file_size,
            })

        except Exception as e:
            results.append({"name": folder_name, "success": False, "reason": str(e)})

    return jsonify({"results": results})

# ================== Mega 디버그 (임시) ==================
@app.route('/mega/debug', methods=['GET'])
def mega_debug():
    mega_email    = os.getenv('MEGA_EMAIL')
    mega_password = os.getenv('MEGA_PASSWORD')
    if not mega_email or not mega_password:
        return jsonify({"error": "Mega credentials not set"}), 500
    try:
        m    = Mega()
        mega = m.login(mega_email, mega_password)
        files = mega.get_files()
    except Exception as e:
        return jsonify({"error": f"Login failed: {str(e)}"}), 500

    folders = []
    total_nodes = len(files)
    for node_id, node in files.items():
        if node.get('t') == 1:  # t=1 이 폴더
            name = None
            if isinstance(node.get('a'), dict):
                name = node['a'].get('n')
            folders.append({"id": node_id, "name": name})

    return jsonify({
        "total_nodes": total_nodes,
        "folder_count": len(folders),
        "folders": folders[:100]  # 최대 100개만
    })

# ================== 헬스체크 ==================
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

# ================== 실행 ==================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
