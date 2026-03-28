import discord
from discord import app_commands
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ================== 채널 ID ==================
CHANNELS = {
    "🇦🇸🇮🇦🇳":     1487319260228358174,
    "🇭🇮🇸🇵🇦🇳🇮🇨": 1487319298681864342,
    "🇧‌🇱‌🇦‌🇨‌🇰‌":  1487319326204625047,
    "🇧🇱🇦🇨🇰":     1487319363265626173,
}

# ================== Content Request ==================
class ContentRequestModal(discord.ui.Modal, title="Content Request Form"):
    name = discord.ui.TextInput(label="Name", placeholder="요청자 이름", required=True, max_length=100)
    link = discord.ui.TextInput(label="Link (OF / X 등)", placeholder="https://...", required=True)
    comment = discord.ui.TextInput(label="Comment", placeholder="추가 설명", required=False, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        request_channel = discord.utils.get(interaction.guild.text_channels, name="🇷🇪🇶🇺🇪🇸🇹")

        if request_channel:
            embed = discord.Embed(
                title="🆕 New Content Request",
                description="A new content request has been submitted!",
                color=0x9b59b6,
                timestamp=datetime.now()
            )
            embed.add_field(name="Requested By", value=interaction.user.mention, inline=True)
            embed.add_field(name="Name", value=self.name.value, inline=True)
            embed.add_field(name="Content Link", value=self.link.value, inline=False)

            if self.comment.value and self.comment.value.strip():
                embed.add_field(name="Comment", value=self.comment.value, inline=False)

            embed.set_footer(text=f"Requested at • {datetime.now().strftime('%Y-%m-%d %H:%M')}")

            await request_channel.send(embed=embed)
            await interaction.response.send_message("✅ 요청이 성공적으로 접수되었습니다!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ request 채널을 찾을 수 없습니다. 관리자에게 문의해주세요.", ephemeral=True)


class RequestButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit Content Request", style=discord.ButtonStyle.primary, emoji="📩", custom_id="submit_request")
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ContentRequestModal())


@tree.command(name="setup-request", description="request-form 채널에 버튼 설정")
async def setup_request(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return

    embed = discord.Embed(
        title="#request-form에 오신 걸 환영합니다!",
        description="#request-form 채널의 시작이에요.",
        color=0x2b2d31
    )
    embed.add_field(
        name="Request Any Model You Want",
        value="Want access to exclusive content?\n\nGet premium access at **xhouse.vip** and unlock:\n• Unlimited model requests\n• Exclusive content library\n• Priority updates",
        inline=False
    )

    view = RequestButtonView()
    await interaction.response.send_message(embed=embed, view=view)


# ================== Post System ==================
class PostModal(discord.ui.Modal, title="New Content Post"):
    post_name = discord.ui.TextInput(label="Name", placeholder="예: Poppi Louiz", required=True, max_length=100)
    file_size = discord.ui.TextInput(label="File Size", placeholder="예: 10GB", required=True, max_length=20)
    key = discord.ui.TextInput(label="Decryption Key", placeholder="복호화 키 입력", required=True, max_length=200)
    link = discord.ui.TextInput(label="VIP Link", placeholder="https://mega.nz/...", required=True)
    image_url = discord.ui.TextInput(label="Image URL", placeholder="https://... (이미지 URL)", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        view = ChannelSelectView(
            post_name=self.post_name.value,
            file_size=self.file_size.value,
            key=self.key.value,
            link=self.link.value,
            image_url=self.image_url.value
        )
        await interaction.response.send_message(
            "📢 포스팅할 채널을 선택해주세요:",
            view=view,
            ephemeral=True
        )


class ChannelSelectView(discord.ui.View):
    def __init__(self, post_name, file_size, key, link, image_url):
        super().__init__(timeout=180)
        self.add_item(ChannelSelect(post_name, file_size, key, link, image_url))


class ChannelSelect(discord.ui.Select):
    def __init__(self, post_name, file_size, key, link, image_url):
        self.post_name = post_name
        self.file_size = file_size
        self.key = key
        self.link = link
        self.image_url = image_url

        options = [discord.SelectOption(label=name, value=str(ch_id)) for name, ch_id in CHANNELS.items()]
        super().__init__(placeholder="채널 선택...", options=options)

    async def callback(self, interaction: discord.Interaction):
        channel_id = int(self.values[0])
        channel = client.get_channel(channel_id)

        if not channel:
            await interaction.response.send_message("❌ 채널을 찾을 수 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(color=0x2b2d31)
        embed.set_image(url=self.image_url)
        embed.add_field(
            name=f"{self.post_name} — {self.file_size}",
            value=f"——————————————————\n🔒 *VIP link hidden*\n\n**Decryption Key:** `{self.key}`\n——————————————————",
            inline=False
        )

        view = RevealLinkView(link=self.link)
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ 포스팅 완료!", ephemeral=True)


class RevealLinkView(discord.ui.View):
    def __init__(self, link: str):
        super().__init__(timeout=None)
        self.link = link

    @discord.ui.button(label="Reveal Link", style=discord.ButtonStyle.primary, emoji="🔓", custom_id="reveal_link")
    async def reveal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"🔗 **Your VIP link:**\n{self.link}",
            ephemeral=True
        )


class PostButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="New Post", style=discord.ButtonStyle.success, emoji="📤", custom_id="new_post")
    async def new_post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
            return
        await interaction.response.send_modal(PostModal())


@tree.command(name="setup-post", description="관리자용 포스팅 버튼 설정")
async def setup_post(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📤 Content Post Panel",
        description="새 콘텐츠를 포스팅하려면 아래 버튼을 클릭하세요.",
        color=0x2b2d31
    )
    view = PostButtonView()
    await interaction.response.send_message(embed=embed, view=view)


# ================== 봇 시작 ==================
@client.event
async def on_ready():
    await tree.sync()
    client.add_view(RequestButtonView())
    client.add_view(PostButtonView())
    print(f"✅ Bot 온라인! ({client.user})")

client.run(TOKEN)
