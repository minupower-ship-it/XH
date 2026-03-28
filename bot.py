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
            print(f"✅ 임베드 전송 성공 → #🇷🇪🇶🇺🇪🇸🇹")

            await interaction.response.send_message("✅ 요청이 성공적으로 접수되었습니다!", ephemeral=True)
        else:
            print("❌ 🇷🇪🇶🇺🇪🇸🇹 채널을 찾을 수 없습니다!")
            await interaction.response.send_message("❌ request 채널을 찾을 수 없습니다. 관리자에게 문의해주세요.", ephemeral=True)


class RequestButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit Content Request", style=discord.ButtonStyle.primary, emoji="📩")
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
        value="Want access to exclusive content?\n\nGet premium access at **phouse.vip** and unlock:\n• Unlimited model requests\n• Exclusive content library\n• Priority updates",
        inline=False
    )

    view = RequestButtonView()
    await interaction.response.send_message(embed=embed, view=view)
    await interaction.followup.send("✅ 버튼이 설정되었습니다!", ephemeral=True)


@client.event
async def on_ready():
    await tree.sync()
    print(f"✅ Request Bot이 온라인되었습니다! (/setup-request 로 버튼 설정)")

client.run(TOKEN)
