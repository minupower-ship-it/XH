import os
import sqlite3
import requests
import random
import string
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import stripe
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

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

# Dummy 채널 (링크 저장 + 코드 저장)
CODE_STORE_CHANNEL        = 1488022953525248141

DISCORD_API = "https://discord.com/api/v10"

# ================== DB 초기화 ==================
def init_db():
    conn = sqlite3.connect('payments.db')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            session_id  TEXT PRIMARY KEY,
            invite_url  TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS s2_payments (
            session_id   TEXT PRIMARY KEY,
            discord_id   TEXT,
            role_granted INTEGER DEFAULT 0,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ================== Discord 헬퍼 ==================
def discord_headers(token):
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

def log_transaction(token: str, channel_id: int, message: str):
    """결제 기록을 Discord 채널에 저장 (동기 방식)"""
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    res = requests.post(url, headers=discord_headers(token), json={"content": message})
    if res.status_code == 200:
        print(f"[TX Log] 저장 완료: {message}")
    else:
        print(f"[TX Log] 저장 실패: {res.status_code}")

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
    """최신 메시지부터 역순으로 조회 (버그 수정 3번)"""
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
    return messages  # Discord는 기본적으로 최신순 반환

# ================== 서버 1: Checkout Session 생성 ==================
@app.route('/create-checkout', methods=['POST'])
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

        conn = sqlite3.connect('payments.db')
        c    = conn.cursor()
        c.execute("SELECT session_id FROM payments WHERE session_id = ?", (session_id,))
        if not c.fetchone():
            c.execute("INSERT INTO payments (session_id) VALUES (?)", (session_id,))
            conn.commit()
            print(f"[S1 Webhook] 결제 저장: {session_id}")

            if discord_id and XHOUSE_ROLE_ID and XHOUSE_GUILD_ID:
                url = f"{DISCORD_API}/guilds/{XHOUSE_GUILD_ID}/members/{discord_id}/roles/{XHOUSE_ROLE_ID}"
                res = requests.put(url, headers=discord_headers(DISCORD_BOT_TOKEN))
                if res.status_code in (200, 204):
                    send_dm(DISCORD_BOT_TOKEN, discord_id, "✅ Payment confirmed! Your membership has been activated. Welcome to X-House! 🎉")
                    print(f"[S1 Webhook] 역할 부여 완료: {discord_id}")
                    log_msg = f"✅ `{session_id}` | <@{discord_id}> | {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
                    log_transaction(DISCORD_BOT_TOKEN, XHOUSE_TX_CHANNEL_ID, log_msg)
                else:
                    print(f"[S1 Webhook] 역할 부여 실패: {res.status_code}")
        conn.close()

    return jsonify(success=True), 200

# ================== 서버 1: 초대링크 발급 ==================
@app.route('/create-invite', methods=['POST'])
def create_invite():
    data       = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"error": "session_id is required."}), 400

    conn = sqlite3.connect('payments.db')
    c    = conn.cursor()
    c.execute("SELECT invite_url FROM payments WHERE session_id = ?", (session_id,))
    row = c.fetchone()

    if not row:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status != 'paid':
                conn.close()
                return jsonify({"error": "Payment not completed."}), 400
            c.execute("INSERT INTO payments (session_id) VALUES (?)", (session_id,))
            conn.commit()
            row = (None,)
        except stripe.error.StripeError as e:
            conn.close()
            return jsonify({"error": f"Payment verification failed: {str(e)}"}), 400

    existing_invite = row[0]
    if existing_invite:
        conn.close()
        return jsonify({"success": True, "invite_url": existing_invite})

    try:
        invite_url = create_discord_invite()
        c.execute("UPDATE payments SET invite_url = ? WHERE session_id = ?", (invite_url, session_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "invite_url": invite_url})
    except Exception as e:
        conn.close()
        print(f"[S1 Invite] 생성 실패: {e}")
        return jsonify({"error": "Failed to create invite link. Please try again."}), 500

# ================== 서버 2: Checkout Session 생성 ==================
@app.route('/s2/create-checkout', methods=['POST'])
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

        conn = sqlite3.connect('payments.db')
        c    = conn.cursor()
        c.execute("SELECT session_id FROM s2_payments WHERE session_id = ?", (session_id,))
        if not c.fetchone():
            c.execute("INSERT INTO s2_payments (session_id, discord_id) VALUES (?, ?)", (session_id, discord_id))
            conn.commit()

            success = assign_role(discord_id)
            if success:
                c.execute("UPDATE s2_payments SET role_granted = 1 WHERE session_id = ?", (session_id,))
                conn.commit()
                send_dm(S2_BOT_TOKEN, discord_id, "✅ Payment confirmed! Your membership has been activated. 🎉")
                print(f"[S2 Webhook] 역할 부여 완료: {discord_id}")
                log_msg = f"✅ `{session_id}` | <@{discord_id}> | {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
                log_transaction(S2_BOT_TOKEN, PBANK_TX_CHANNEL_ID, log_msg)
            else:
                print(f"[S2 Webhook] 역할 부여 실패: {discord_id}")
        conn.close()

    return jsonify(success=True), 200

# ================== 코드 발급/검증 (텔레그램 봇용 - 보류) ==================
def generate_code():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=10))

@app.route('/issue-code', methods=['POST'])
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
def mega_scan():
    from mega import Mega

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

    results = []

    for folder_name in folder_names:
        try:
            folder = mega.find(folder_name)
            if not folder:
                results.append({"name": folder_name, "success": False, "reason": "Folder not found in Mega"})
                continue

            folder_node = list(folder.values())[0] if isinstance(folder, dict) else folder
            link        = mega.get_link(folder_node)

            if not link:
                results.append({"name": folder_name, "success": False, "reason": "Failed to get folder link"})
                continue

            key = link.split('#')[-1] if '#' in link else ""

            total_bytes = 0
            for f in files.values():
                if f.get('t') == 0 and f.get('p') == folder_node.get('h'):
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

# ================== 헬스체크 ==================
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

# ================== 실행 ==================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
