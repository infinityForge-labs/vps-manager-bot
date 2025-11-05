import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import aiohttp
import aiofiles
import os
import json
import sqlite3
import secrets
import string
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, List
import psutil
from dotenv import load_dotenv
import logging

# ============================================
# 🔧 LOGGING SETUP
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('vps_manager.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('VPSManager')

# ============================================
# 🌍 LOAD ENVIRONMENT VARIABLES
# ============================================

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
ADMIN_ROLE_ID = int(os.getenv('ADMIN_ROLE_ID', '0'))
DEFAULT_HOSTNAME = os.getenv('DEFAULT_HOSTNAME', 'localhost')
MAX_VPS_PER_USER = int(os.getenv('MAX_VPS_PER_USER', '5'))
VM_DIR = os.getenv('VM_DIR', os.path.expanduser("~/vms"))

# Validate required environment variables
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN not found in environment variables!")
    exit(1)

if not OWNER_ID:
    logger.warning("⚠️ OWNER_ID not set. Admin commands will be restricted.")

# ============================================
# 🎨 CONFIGURATION & CONSTANTS
# ============================================

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)
bot.owner_id = OWNER_ID

# Database
DB_FILE = "vps_manager.db"
os.makedirs(VM_DIR, exist_ok=True)

# OS Images Configuration
OS_IMAGES = {
    "ubuntu22": {
        "name": "🐧 Ubuntu 22.04 LTS",
        "image": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
        "codename": "jammy",
        "default_user": "ubuntu"
    },
    "ubuntu24": {
        "name": "🐧 Ubuntu 24.04 LTS",
        "image": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "codename": "noble",
        "default_user": "ubuntu"
    },
    "debian11": {
        "name": "🌀 Debian 11 (Bullseye)",
        "image": "https://cloud.debian.org/images/cloud/bullseye/latest/debian-11-generic-amd64.qcow2",
        "codename": "bullseye",
        "default_user": "debian"
    },
    "debian12": {
        "name": "🌀 Debian 12 (Bookworm)",
        "image": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2",
        "codename": "bookworm",
        "default_user": "debian"
    },
    "fedora40": {
        "name": "🎩 Fedora 40",
        "image": "https://download.fedoraproject.org/pub/fedora/linux/releases/40/Cloud/x86_64/images/Fedora-Cloud-Base-40-1.14.x86_64.qcow2",
        "codename": "40",
        "default_user": "fedora"
    },
    "centos9": {
        "name": "💼 CentOS Stream 9",
        "image": "https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2",
        "codename": "stream9",
        "default_user": "centos"
    },
    "alma9": {
        "name": "🔷 AlmaLinux 9",
        "image": "https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/AlmaLinux-9-GenericCloud-latest.x86_64.qcow2",
        "codename": "9",
        "default_user": "alma"
    },
    "rocky9": {
        "name": "⛰️ Rocky Linux 9",
        "image": "https://download.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud.latest.x86_64.qcow2",
        "codename": "9",
        "default_user": "rocky"
    }
}

# ============================================
# 🗄️ DATABASE FUNCTIONS
# ============================================

def init_database():
    """Initialize SQLite database with all required tables"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # VPS Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vps_id TEXT UNIQUE NOT NULL,
            owner_id INTEGER NOT NULL,
            hostname TEXT NOT NULL,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            ssh_port INTEGER NOT NULL,
            memory INTEGER NOT NULL,
            cpus INTEGER NOT NULL,
            disk_size TEXT NOT NULL,
            os_type TEXT NOT NULL,
            image_file TEXT NOT NULL,
            seed_file TEXT NOT NULL,
            status TEXT DEFAULT 'stopped',
            pid INTEGER DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            gui_mode INTEGER DEFAULT 0,
            port_forwards TEXT
        )
    """)
    
    # Admins Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Banned Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY,
            banned_by INTEGER NOT NULL,
            reason TEXT,
            banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Statistics Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS statistics (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    
    # Initialize default statistics
    cursor.execute("INSERT OR IGNORE INTO statistics VALUES ('total_vps_created', '0')")
    cursor.execute("INSERT OR IGNORE INTO statistics VALUES ('total_restarts', '0')")
    cursor.execute("INSERT OR IGNORE INTO statistics VALUES ('total_downloads', '0')")
    
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized successfully")

def generate_vps_id() -> str:
    """Generate unique VPS ID"""
    return f"vps_{secrets.token_hex(8)}"

def generate_password(length: int = 16) -> str:
    """Generate secure random password"""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(chars) for _ in range(length))

# ============================================
# 🔐 PERMISSION CHECKS
# ============================================

def is_owner(user_id: int) -> bool:
    """Check if user is bot owner"""
    return user_id == OWNER_ID

def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    if is_owner(user_id):
        return True
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def has_admin_role(member: discord.Member) -> bool:
    """Check if member has admin role"""
    if ADMIN_ROLE_ID == 0:
        return False
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)

def is_banned(user_id: int) -> bool:
    """Check if user is banned"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM banned_users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def can_create_vps(user_id: int) -> tuple[bool, str]:
    """Check if user can create more VPS"""
    if is_admin(user_id):
        return True, ""
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM vps WHERE owner_id = ?", (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    
    if count >= MAX_VPS_PER_USER:
        return False, f"You have reached the maximum limit of {MAX_VPS_PER_USER} VPS instances."
    return True, ""

# ============================================
# 📥 ASYNC DOWNLOAD FUNCTION
# ============================================

async def download_image_async(url: str, output_path: str, callback=None) -> bool:
    """Download image file asynchronously with progress tracking"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to download image: HTTP {response.status}")
                    return False
                
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                
                async with aiofiles.open(output_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(1024 * 1024):  # 1MB chunks
                        await f.write(chunk)
                        downloaded += len(chunk)
                        
                        if callback and total_size > 0:
                            progress = (downloaded / total_size) * 100
                            await callback(progress)
                
                logger.info(f"✅ Downloaded image: {output_path}")
                return True
    except Exception as e:
        logger.error(f"❌ Download failed: {str(e)}")
        return False

# ============================================
# 🖥️ VPS MANAGEMENT FUNCTIONS
# ============================================

async def create_vps_instance(
    owner_id: int,
    memory: int,
    cpus: int,
    disk_size: str,
    hostname: str,
    username: str,
    password: str,
    os_type: str = "ubuntu22",
    gui_mode: bool = False,
    port_forwards: str = ""
) -> Dict:
    """Create a new VPS instance asynchronously"""
    
    vps_id = generate_vps_id()
    ssh_port = await find_free_port()
    
    # Get OS configuration
    os_config = OS_IMAGES.get(os_type, OS_IMAGES["ubuntu22"])
    
    # File paths
    img_file = f"{VM_DIR}/{vps_id}.img"
    seed_file = f"{VM_DIR}/{vps_id}-seed.iso"
    
    # Check if we need to download
    cache_file = f"{VM_DIR}/cache_{os_type}.img"
    
    if not os.path.exists(cache_file):
        logger.info(f"📥 Downloading {os_config['name']}...")
        success = await download_image_async(os_config["image"], cache_file)
        if not success:
            raise Exception("Failed to download OS image")
        
        # Update statistics
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE statistics SET value = CAST(value AS INTEGER) + 1 WHERE key = 'total_downloads'")
        conn.commit()
        conn.close()
    
    # Copy cached image to VPS image
    logger.info(f"📋 Creating VPS image from cache...")
    process = await asyncio.create_subprocess_exec(
        'cp', cache_file, img_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()
    
    # Get current disk size
    process = await asyncio.create_subprocess_exec(
        'qemu-img', 'info', '--output=json', img_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode == 0:
        import json
        info = json.loads(stdout.decode())
        current_size = info.get('virtual-size', 0)
        
        # Parse target size (convert G to bytes)
        target_size_str = disk_size.upper()
        if target_size_str.endswith('G'):
            target_size = int(target_size_str[:-1]) * 1024 * 1024 * 1024
        elif target_size_str.endswith('M'):
            target_size = int(target_size_str[:-1]) * 1024 * 1024
        else:
            target_size = current_size
        
        # Only resize if target is larger
        if target_size > current_size:
            logger.info(f"💾 Resizing disk from {current_size / 1024 / 1024 / 1024:.1f}G to {disk_size}...")
            process = await asyncio.create_subprocess_exec(
                'qemu-img', 'resize', img_file, disk_size,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.warning(f"⚠️ Resize warning: {stderr.decode()}")
        else:
            logger.info(f"💾 Disk size {disk_size} is equal or smaller than current size, skipping resize")
    else:
        logger.warning(f"⚠️ Could not get image info: {stderr.decode()}")
    
    # Create cloud-init config
    user_data = f"""#cloud-config
hostname: {hostname}
ssh_pwauth: true
disable_root: false
users:
  - name: {username}
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
chpasswd:
  list: |
    root:{password}
    {username}:{password}
  expire: false
packages:
  - vim
  - curl
  - wget
  - htop
  - net-tools
runcmd:
  - echo '╔══════════════════════════════════════╗' > /etc/motd
  - echo '║   🚀 HOPINGBOYZ VPS MANAGER         ║' >> /etc/motd
  - echo '║   Welcome to {hostname}             ║' >> /etc/motd
  - echo '╚══════════════════════════════════════╝' >> /etc/motd
"""
    
    meta_data = f"""instance-id: iid-{vps_id}
local-hostname: {hostname}
"""
    
    # Write cloud-init files
    async with aiofiles.open(f"{VM_DIR}/user-data-{vps_id}", "w") as f:
        await f.write(user_data)
    async with aiofiles.open(f"{VM_DIR}/meta-data-{vps_id}", "w") as f:
        await f.write(meta_data)
    
    # Create seed ISO
    logger.info(f"💿 Creating cloud-init ISO...")
    process = await asyncio.create_subprocess_exec(
        'cloud-localds', seed_file,
        f"{VM_DIR}/user-data-{vps_id}",
        f"{VM_DIR}/meta-data-{vps_id}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        raise Exception(f"Failed to create seed ISO: {stderr.decode()}")
    
    # Save to database
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO vps (vps_id, owner_id, hostname, username, password, ssh_port,
                        memory, cpus, disk_size, os_type, image_file, seed_file,
                        gui_mode, port_forwards)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (vps_id, owner_id, hostname, username, password, ssh_port, memory, cpus,
          disk_size, os_type, img_file, seed_file, 1 if gui_mode else 0, port_forwards))
    
    # Update statistics
    cursor.execute("UPDATE statistics SET value = CAST(value AS INTEGER) + 1 WHERE key = 'total_vps_created'")
    
    conn.commit()
    conn.close()
    
    # Cleanup temp files
    try:
        os.remove(f"{VM_DIR}/user-data-{vps_id}")
        os.remove(f"{VM_DIR}/meta-data-{vps_id}")
    except:
        pass
    
    logger.info(f"✅ VPS {vps_id} created successfully")
    
    return {
        "vps_id": vps_id,
        "ssh_port": ssh_port,
        "hostname": hostname,
        "username": username,
        "password": password,
        "os_type": os_type
    }

async def start_vps(vps_id: str) -> bool:
    """Start a VPS instance"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vps WHERE vps_id = ?", (vps_id,))
    vps = cursor.fetchone()
    conn.close()
    
    if not vps:
        logger.error(f"VPS {vps_id} not found in database")
        return False
    
    # Check if image files exist
    if not os.path.exists(vps[11]):
        logger.error(f"Image file not found: {vps[11]}")
        return False
    
    if not os.path.exists(vps[12]):
        logger.error(f"Seed file not found: {vps[12]}")
        return False
    
    # Build QEMU command
    pidfile = f"{VM_DIR}/{vps_id}.pid"
    logfile = f"{VM_DIR}/{vps_id}.log"
    
    cmd = [
        "qemu-system-x86_64",
        "-enable-kvm",
        "-m", str(vps[7]),  # memory
        "-smp", str(vps[8]),  # cpus
        "-cpu", "host",
        "-drive", f"file={vps[11]},format=qcow2,if=virtio",
        "-drive", f"file={vps[12]},format=raw,if=virtio",
        "-boot", "order=c",
        "-device", "virtio-net-pci,netdev=n0",
        "-netdev", f"user,id=n0,hostfwd=tcp::{vps[6]}-:22",
        "-daemonize",
        "-pidfile", pidfile
    ]
    
    if vps[16]:  # gui_mode
        cmd.extend(["-vga", "virtio", "-display", "gtk,gl=on"])
    else:
        cmd.extend(["-nographic", "-serial", f"file:{logfile}"])
    
    # Add port forwards
    if vps[17]:
        forwards = vps[17].split(',')
        for i, forward in enumerate(forwards, 1):
            if ':' in forward:
                try:
                    host_port, guest_port = forward.split(':')
                    cmd.extend([
                        "-device", f"virtio-net-pci,netdev=n{i}",
                        "-netdev", f"user,id=n{i},hostfwd=tcp::{host_port}-:{guest_port}"
                    ])
                except ValueError:
                    logger.warning(f"Invalid port forward format: {forward}")
    
    # Start QEMU
    try:
        logger.info(f"Starting QEMU with command: {' '.join(cmd)}")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            logger.error(f"QEMU failed to start: {error_msg}")
            return False
        
        # Wait for PID file to be created
        max_attempts = 10
        for attempt in range(max_attempts):
            if os.path.exists(pidfile):
                break
            await asyncio.sleep(0.5)
        
        if not os.path.exists(pidfile):
            logger.error(f"PID file was not created: {pidfile}")
            # Try to find process by command
            try:
                result = await asyncio.create_subprocess_shell(
                    f"pgrep -f 'qemu.*{vps_id}'",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await result.communicate()
                if stdout:
                    pid = int(stdout.decode().strip().split('\n')[0])
                    logger.info(f"Found QEMU process via pgrep: PID {pid}")
                else:
                    logger.error("Could not find QEMU process")
                    return False
            except Exception as e:
                logger.error(f"Failed to find QEMU process: {str(e)}")
                return False
        else:
            # Read PID from file
            async with aiofiles.open(pidfile, "r") as f:
                pid_str = await f.read()
                pid = int(pid_str.strip())
        
        # Verify process is running
        try:
            os.kill(pid, 0)  # Signal 0 just checks if process exists
        except OSError:
            logger.error(f"Process {pid} is not running")
            return False
        
        # Update database
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE vps SET status = 'running', pid = ? WHERE vps_id = ?", (pid, vps_id))
        conn.commit()
        conn.close()
        
        logger.info(f"✅ VPS {vps_id} started successfully (PID: {pid})")
        return True
        
    except FileNotFoundError:
        logger.error("qemu-system-x86_64 not found. Please install QEMU.")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to start VPS {vps_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False

async def stop_vps(vps_id: str) -> bool:
    """Stop a VPS instance"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT pid FROM vps WHERE vps_id = ?", (vps_id,))
    result = cursor.fetchone()
    
    if not result or not result[0]:
        conn.close()
        return False
    
    pid = result[0]
    
    try:
        # Send SIGTERM
        os.kill(pid, 15)
        await asyncio.sleep(5)
        
        # Check if still running
        try:
            os.kill(pid, 0)
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        
        # Update database
        cursor.execute("UPDATE vps SET status = 'stopped', pid = NULL WHERE vps_id = ?", (vps_id,))
        conn.commit()
        conn.close()
        
        logger.info(f"✅ VPS {vps_id} stopped")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to stop VPS {vps_id}: {str(e)}")
        conn.close()
        return False

async def find_free_port(start: int = 2222, end: int = 65535) -> int:
    """Find a free port for SSH"""
    import socket
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return port
            except OSError:
                continue
    return 2222

def get_user_vps(user_id: int) -> List[tuple]:
    """Get all VPS owned by user"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if user_id == 0:
        cursor.execute("SELECT * FROM vps")
    else:
        cursor.execute("SELECT * FROM vps WHERE owner_id = ?", (user_id,))
    vps_list = cursor.fetchall()
    conn.close()
    return vps_list

def get_vps_by_id(vps_id: str) -> Optional[tuple]:
    """Get VPS by ID"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vps WHERE vps_id = ?", (vps_id,))
    vps = cursor.fetchone()
    conn.close()
    return vps

# ============================================
# 🎨 EMBED BUILDERS
# ============================================

def create_success_embed(title: str, description: str) -> discord.Embed:
    """Create success embed"""
    embed = discord.Embed(
        title=f"✅ {title}",
        description=description,
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(
        text="🚀 HOPINGBOYZ VPS Manager",
        icon_url="https://cdn.discordapp.com/emojis/1234567890.png" if bot.user and bot.user.avatar else None
    )
    return embed

def create_error_embed(title: str, description: str) -> discord.Embed:
    """Create error embed"""
    embed = discord.Embed(
        title=f"❌ {title}",
        description=description,
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    return embed

def create_info_embed(title: str, description: str) -> discord.Embed:
    """Create info embed"""
    embed = discord.Embed(
        title=f"ℹ️ {title}",
        description=description,
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    return embed

def create_warning_embed(title: str, description: str) -> discord.Embed:
    """Create warning embed"""
    embed = discord.Embed(
        title=f"⚠️ {title}",
        description=description,
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    return embed

def create_vps_info_embed(vps: tuple) -> discord.Embed:
    """Create VPS info embed"""
    os_name = OS_IMAGES.get(vps[10], {}).get("name", vps[10])
    status_emoji = "🟢" if vps[13] == "running" else "🔴"
    
    embed = discord.Embed(
        title=f"🖥️ VPS Information",
        description=f"**{vps[3]}** (`{vps[1]}`)",
        color=discord.Color.blue() if vps[13] == "running" else discord.Color.greyple(),
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.add_field(
        name="📊 Status",
        value=f"{status_emoji} **{vps[13].upper()}**",
        inline=True
    )
    embed.add_field(
        name="💻 Operating System",
        value=os_name,
        inline=True
    )
    embed.add_field(
        name="🆔 VPS ID",
        value=f"`{vps[1]}`",
        inline=True
    )
    
    embed.add_field(
        name="👤 Username",
        value=f"`{vps[4]}`",
        inline=True
    )
    embed.add_field(
        name="🔑 Password",
        value=f"||`{vps[5]}`||",
        inline=True
    )
    embed.add_field(
        name="🔌 SSH Port",
        value=f"`{vps[6]}`",
        inline=True
    )
    
    embed.add_field(
        name="🧠 Memory",
        value=f"`{vps[7]} MB`",
        inline=True
    )
    embed.add_field(
        name="⚡ CPU Cores",
        value=f"`{vps[8]}`",
        inline=True
    )
    embed.add_field(
        name="💾 Disk Size",
        value=f"`{vps[9]}`",
        inline=True
    )
    
    created_ts = int(datetime.fromisoformat(vps[15]).timestamp())
    embed.add_field(
        name="📅 Created",
        value=f"<t:{created_ts}:R>",
        inline=False
    )
    
    if vps[13] == "running":
        embed.add_field(
            name="🔗 SSH Connection",
            value=f"```bash\nssh -p {vps[6]} {vps[4]}@{DEFAULT_HOSTNAME}```",
            inline=False
        )
    
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    return embed

# ============================================
# 📱 BOT EVENTS
# ============================================

@bot.event
async def on_ready():
    """Bot startup event"""
    logger.info("=" * 70)
    logger.info("🚀 HOPINGBOYZ VPS MANAGER BOT")
    logger.info("=" * 70)
    logger.info(f"👤 Logged in as: {bot.user.name}#{bot.user.discriminator}")
    logger.info(f"🆔 Bot ID: {bot.user.id}")
    logger.info(f"🌐 Servers: {len(bot.guilds)}")
    logger.info(f"👑 Owner ID: {OWNER_ID}")
    logger.info(f"💾 VM Directory: {VM_DIR}")
    logger.info(f"📊 Max VPS per user: {MAX_VPS_PER_USER}")
    logger.info("✅ Status: ONLINE")
    logger.info("=" * 70)
    
    init_database()
    
    try:
        await bot.tree.sync()
        logger.info("✅ Slash commands synced successfully")
    except Exception as e:
        logger.error(f"❌ Failed to sync commands: {str(e)}")
    
    status_updater.start()

@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Command error: {str(error)}")

@tasks.loop(seconds=30)
async def status_updater():
    """Update bot status"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM vps WHERE status = 'running'")
        running = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM vps")
        total = cursor.fetchone()[0]
        conn.close()
        
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{running}/{total} VPS Running | /help"
            ),
            status=discord.Status.online
        )
    except Exception as e:
        logger.error(f"Status update error: {str(e)}")

# ============================================
# 💬 SLASH COMMANDS - USER
# ============================================

@bot.tree.command(name="help", description="📚 Show all available commands")
async def help_command(interaction: discord.Interaction):
    """Display help information"""
    embed = discord.Embed(
        title="📚 HOPINGBOYZ VPS Manager",
        description="```Professional Virtual Private Server Management Platform```",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else None)
    
    embed.add_field(
        name="👤 User Commands",
        value=(
            "```yml\n"
            "/create_vps  : Create a new VPS instance\n"
            "/list        : View all your VPS instances\n"
            "/vps_info    : Get detailed VPS information\n"
            "/start_vps   : Start a stopped VPS\n"
            "/stop_vps    : Stop a running VPS\n"
            "/restart_vps : Restart your VPS\n"
            "/delete_vps  : Delete your VPS\n"
            "/change_pass : Change VPS SSH password\n"
            "/vps_stats   : View VPS resource usage\n"
            "/vps_logs    : View VPS console logs\n"
            "/vps_shell   : Get SSH connection details\n"
            "```"
        ),
        inline=False
    )
    
    if is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user)):
        embed.add_field(
            name="👑 Admin Commands",
            value=(
                "```yml\n"
                "/admin_list    : View all VPS platform-wide\n"
                "/admin_stats   : System statistics dashboard\n"
                "/ban_user      : Ban user from VPS creation\n"
                "/unban_user    : Unban a user\n"
                "/list_banned   : View all banned users\n"
                "/add_admin     : Grant admin permissions\n"
                "/remove_admin  : Revoke admin permissions\n"
                "/force_stop    : Force stop any VPS\n"
                "/cleanup       : Clean orphaned VPS files\n"
                "```"
            ),
            inline=False
        )
    
    embed.add_field(
        name="📊 Features",
        value=(
            "```diff\n"
            "+ 8 Linux distributions available\n"
            "+ Async operations (no blocking)\n"
            "+ Resource monitoring\n"
            "+ Secure password generation\n"
            "+ Auto status updates\n"
            "+ Admin role support\n"
            "```"
        ),
        inline=True
    )
    
    embed.add_field(
        name="🔐 Security",
        value=(
            "```diff\n"
            "+ User ban system\n"
            "+ Admin management\n"
            "+ VPS limits per user\n"
            "+ Encrypted passwords\n"
            "```"
        ),
        inline=True
    )
    
    embed.set_footer(text=f"🚀 HOPINGBOYZ VPS Manager | Max {MAX_VPS_PER_USER} VPS per user")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="create_vps", description="🚀 Create a new VPS instance")
@app_commands.describe(
    memory="Memory in MB (512-8192)",
    cpus="CPU cores (1-8)",
    disk="Disk size (e.g., 20G)",
    os_type="Operating system"
)
@app_commands.choices(os_type=[
    app_commands.Choice(name="🐧 Ubuntu 22.04 LTS", value="ubuntu22"),
    app_commands.Choice(name="🐧 Ubuntu 24.04 LTS", value="ubuntu24"),
    app_commands.Choice(name="🌀 Debian 11", value="debian11"),
    app_commands.Choice(name="🌀 Debian 12", value="debian12"),
    app_commands.Choice(name="🎩 Fedora 40", value="fedora40"),
    app_commands.Choice(name="💼 CentOS Stream 9", value="centos9"),
    app_commands.Choice(name="🔷 AlmaLinux 9", value="alma9"),
    app_commands.Choice(name="⛰️ Rocky Linux 9", value="rocky9"),
])
async def create_vps(
    interaction: discord.Interaction,
    memory: int,
    cpus: int,
    disk: str,
    os_type: str = "ubuntu22"
):
    """Create a new VPS instance"""
    
    # Check if banned
    if is_banned(interaction.user.id):
        embed = create_error_embed(
            "Access Denied",
            "🚫 You are banned from creating VPS instances.\n\nContact an administrator for more information."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Check VPS limit
    can_create, message = can_create_vps(interaction.user.id)
    if not can_create:
        embed = create_error_embed("VPS Limit Reached", f"❌ {message}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Validate inputs
    if memory < 512 or memory > 8192:
        embed = create_error_embed(
            "Invalid Memory",
            "⚠️ Memory must be between **512 MB** and **8192 MB**"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if cpus < 1 or cpus > 8:
        embed = create_error_embed(
            "Invalid CPU Count",
            "⚠️ CPU count must be between **1** and **8**"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Defer response
    await interaction.response.defer(thinking=True)
    
    try:
        # Generate credentials
        hostname = f"vps-{interaction.user.name.lower()}-{secrets.token_hex(3)}"
        username = OS_IMAGES[os_type]["default_user"]
        password = generate_password()
        
        # Create progress embed
        progress_embed = create_info_embed(
            "Creating VPS...",
            f"⏳ Please wait while we set up your VPS\n\n"
            f"```yml\n"
            f"Hostname : {hostname}\n"
            f"OS       : {OS_IMAGES[os_type]['name']}\n"
            f"Memory   : {memory} MB\n"
            f"CPUs     : {cpus}\n"
            f"Disk     : {disk}\n"
            f"```\n"
            f"📥 Downloading OS image..."
        )
        await interaction.followup.send(embed=progress_embed)
        
        # Create VPS
        vps_data = await create_vps_instance(
            owner_id=interaction.user.id,
            memory=memory,
            cpus=cpus,
            disk_size=disk,
            hostname=hostname,
            username=username,
            password=password,
            os_type=os_type
        )
        
        # Success embed
        embed = create_success_embed(
            "VPS Created Successfully!",
            f"🎉 Your VPS **{hostname}** is ready!\n\n"
            f"Use `/start_vps` to boot it up."
        )
        
        embed.add_field(
            name="🆔 VPS Identifier",
            value=f"```{vps_data['vps_id']}```",
            inline=False
        )
        
        embed.add_field(
            name="💻 System Specifications",
            value=(
                f"```yml\n"
                f"OS     : {OS_IMAGES[os_type]['name']}\n"
                f"Memory : {memory} MB\n"
                f"CPUs   : {cpus} cores\n"
                f"Disk   : {disk}\n"
                f"```"
            ),
            inline=True
        )
        
        embed.add_field(
            name="🔐 Access Credentials",
            value=(
                f"```yml\n"
                f"Host     : {DEFAULT_HOSTNAME}\n"
                f"Port     : {vps_data['ssh_port']}\n"
                f"Username : {username}\n"
                f"Password : {password}\n"
                f"```"
            ),
            inline=True
        )
        
        embed.add_field(
            name="🔗 SSH Connection",
            value=f"```bash\nssh -p {vps_data['ssh_port']} {username}@{DEFAULT_HOSTNAME}```",
            inline=False
        )
        
        embed.add_field(
            name="⚡ Quick Start",
            value=(
                f"1️⃣ Use `/start_vps {vps_data['vps_id']}`\n"
                f"2️⃣ Wait 30-60 seconds for boot\n"
                f"3️⃣ Connect via SSH using credentials above"
            ),
            inline=False
        )
        
        await interaction.edit_original_response(embed=embed)
        logger.info(f"✅ VPS created for user {interaction.user.id}: {vps_data['vps_id']}")
        
    except Exception as e:
        embed = create_error_embed(
            "VPS Creation Failed",
            f"❌ An error occurred while creating your VPS:\n\n```{str(e)}```\n\n"
            f"Please contact an administrator if this persists."
        )
        await interaction.edit_original_response(embed=embed)
        logger.error(f"❌ VPS creation failed: {str(e)}")

@bot.tree.command(name="list", description="📋 View all your VPS instances")
async def list_vps(interaction: discord.Interaction):
    """List all VPS owned by user"""
    vps_list = get_user_vps(interaction.user.id)
    
    if not vps_list:
        embed = create_info_embed(
            "No VPS Found",
            "📦 You don't have any VPS instances yet.\n\n"
            "Use `/create_vps` to create your first VPS!"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"🖥️ Your VPS Instances",
        description=f"```Total: {len(vps_list)} VPS | Limit: {MAX_VPS_PER_USER}```",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    
    for vps in vps_list:
        status_emoji = "🟢" if vps[13] == "running" else "🔴"
        os_name = OS_IMAGES.get(vps[10], {}).get("name", vps[10])
        created_ts = int(datetime.fromisoformat(vps[15]).timestamp())
        
        value = (
            f"{status_emoji} **Status:** `{vps[13].upper()}`\n"
            f"💻 **OS:** {os_name}\n"
            f"🧠 **RAM:** `{vps[7]} MB` | ⚡ **CPU:** `{vps[8]}` | 💾 **Disk:** `{vps[9]}`\n"
            f"🔌 **Port:** `{vps[6]}` | 📅 **Created:** <t:{created_ts}:R>\n"
            f"🆔 **ID:** `{vps[1]}`"
        )
        
        embed.add_field(
            name=f"╔═ {vps[3]}",
            value=value,
            inline=False
        )
    
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="start_vps", description="▶️ Start a VPS instance")
@app_commands.describe(vps_id="The ID of the VPS to start")
async def start_vps_command(interaction: discord.Interaction, vps_id: str):
    """Start a VPS instance"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[13] == "running":
        embed = create_info_embed("Already Running", f"🟢 VPS **{vps[3]}** is already running!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)
    
    embed = create_info_embed(
        "Starting VPS...",
        f"⏳ Booting up **{vps[3]}**\n\nThis may take a few moments..."
    )
    await interaction.followup.send(embed=embed)
    
    success = await start_vps(vps_id)
    
    if success:
        embed = create_success_embed(
            "VPS Started Successfully!",
            f"🟢 VPS **{vps[3]}** is now running!\n\n"
            f"⏰ Wait **30-60 seconds** for the system to fully boot."
        )
        embed.add_field(
            name="🔗 SSH Connection",
            value=f"```bash\nssh -p {vps[6]} {vps[4]}@{DEFAULT_HOSTNAME}```",
            inline=False
        )
        embed.add_field(name="👤 Username", value=f"`{vps[4]}`", inline=True)
        embed.add_field(name="🔑 Password", value=f"||`{vps[5]}`||", inline=True)
        embed.add_field(name="🔌 SSH Port", value=f"`{vps[6]}`", inline=True)
    else:
        embed = create_error_embed(
            "Failed to Start VPS",
            f"❌ Could not start VPS **{vps[3]}**\n\n"
            f"Please check system logs or contact an administrator."
        )
    
    await interaction.edit_original_response(embed=embed)

@bot.tree.command(name="stop_vps", description="⏹️ Stop a running VPS")
@app_commands.describe(vps_id="The ID of the VPS to stop")
async def stop_vps_command(interaction: discord.Interaction, vps_id: str):
    """Stop a VPS instance"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[13] == "stopped":
        embed = create_info_embed("Already Stopped", f"🔴 VPS **{vps[3]}** is already stopped!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)
    
    success = await stop_vps(vps_id)
    
    if success:
        embed = create_success_embed(
            "VPS Stopped Successfully!",
            f"🔴 VPS **{vps[3]}** has been shut down.\n\n"
            f"Use `/start_vps {vps_id}` to boot it back up."
        )
    else:
        embed = create_error_embed(
            "Failed to Stop VPS",
            f"❌ Could not stop VPS **{vps[3]}**\n\n"
            f"Please contact an administrator."
        )
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="restart_vps", description="🔄 Restart a VPS instance")
@app_commands.describe(vps_id="The ID of the VPS to restart")
async def restart_vps_command(interaction: discord.Interaction, vps_id: str):
    """Restart a VPS instance"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)
    
    embed = create_info_embed(
        "Restarting VPS...",
        f"🔄 Restarting **{vps[3]}**\n\nPlease wait..."
    )
    await interaction.followup.send(embed=embed)
    
    # Stop if running
    if vps[13] == "running":
        await stop_vps(vps_id)
        await asyncio.sleep(3)
    
    # Start VPS
    success = await start_vps(vps_id)
    
    # Update restart counter
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE statistics SET value = CAST(value AS INTEGER) + 1 WHERE key = 'total_restarts'")
    conn.commit()
    conn.close()
    
    if success:
        embed = create_success_embed(
            "VPS Restarted Successfully!",
            f"🔄 VPS **{vps[3]}** has been restarted!\n\n"
            f"⏰ Wait **30-60 seconds** for the system to fully boot."
        )
        embed.add_field(
            name="🔗 SSH Connection",
            value=f"```bash\nssh -p {vps[6]} {vps[4]}@{DEFAULT_HOSTNAME}```",
            inline=False
        )
    else:
        embed = create_error_embed(
            "Failed to Restart VPS",
            f"❌ Could not restart VPS **{vps[3]}**"
        )
    
    await interaction.edit_original_response(embed=embed)

@bot.tree.command(name="vps_info", description="ℹ️ Get detailed information about a VPS")
@app_commands.describe(vps_id="The ID of the VPS")
async def vps_info_command(interaction: discord.Interaction, vps_id: str):
    """Get VPS information"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = create_vps_info_embed(vps)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="delete_vps", description="🗑️ Delete a VPS instance")
@app_commands.describe(vps_id="The ID of the VPS to delete")
async def delete_vps_command(interaction: discord.Interaction, vps_id: str):
    """Delete a VPS instance"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Confirmation view
    class ConfirmView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            self.value = None
        
        @discord.ui.button(label="✅ Confirm Delete", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.value = True
            await interaction.response.defer()
            self.stop()
        
        @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.value = False
            await interaction.response.defer()
            self.stop()
    
    view = ConfirmView()
    
    embed = create_warning_embed(
        "Confirm VPS Deletion",
        f"⚠️ Are you sure you want to delete VPS **{vps[3]}**?\n\n"
        f"```diff\n"
        f"- This action cannot be undone!\n"
        f"- All data will be permanently lost!\n"
        f"- VPS ID: {vps[1]}\n"
        f"```"
    )
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    await view.wait()
    
    if view.value:
        # Stop VPS if running
        if vps[13] == "running":
            await stop_vps(vps_id)
        
        # Delete files
        try:
            if os.path.exists(vps[11]):
                os.remove(vps[11])
            if os.path.exists(vps[12]):
                os.remove(vps[12])
            pid_file = f"{VM_DIR}/{vps_id}.pid"
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except Exception as e:
            logger.error(f"Error deleting files: {str(e)}")
        
        # Delete from database
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vps WHERE vps_id = ?", (vps_id,))
        conn.commit()
        conn.close()
        
        embed = create_success_embed(
            "VPS Deleted Successfully!",
            f"🗑️ VPS **{vps[3]}** has been permanently deleted.\n\n"
            f"All associated data has been removed."
        )
        await interaction.edit_original_response(embed=embed, view=None)
        logger.info(f"✅ VPS {vps_id} deleted by user {interaction.user.id}")
    else:
        embed = create_info_embed(
            "Deletion Cancelled",
            "✋ VPS deletion has been cancelled.\n\nYour VPS is safe!"
        )
        await interaction.edit_original_response(embed=embed, view=None)

@bot.tree.command(name="change_pass", description="🔐 Change VPS SSH password")
@app_commands.describe(vps_id="The ID of the VPS")
async def change_password_command(interaction: discord.Interaction, vps_id: str):
    """Change VPS SSH password"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Generate new password
    new_password = generate_password()
    
    # Update database
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE vps SET password = ? WHERE vps_id = ?", (new_password, vps_id))
    conn.commit()
    conn.close()
    
    embed = create_success_embed(
        "Password Changed Successfully!",
        f"🔐 New SSH password generated for VPS **{vps[3]}**"
    )
    embed.add_field(
        name="🔑 New Password",
        value=f"||`{new_password}`||",
        inline=False
    )
    embed.add_field(
        name="⚠️ Important Note",
        value=(
            "The password in the VPS will be updated on the **next restart**.\n\n"
            f"Use `/restart_vps {vps_id}` to apply the new password."
        ),
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info(f"✅ Password changed for VPS {vps_id}")

@bot.tree.command(name="vps_stats", description="📊 View VPS resource statistics")
@app_commands.describe(vps_id="The ID of the VPS")
async def vps_stats_command(interaction: discord.Interaction, vps_id: str):
    """View VPS statistics"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"📊 VPS Statistics",
        description=f"**{vps[3]}** (`{vps[1]}`)",
        color=discord.Color.blue() if vps[13] == "running" else discord.Color.greyple(),
        timestamp=datetime.now(timezone.utc)
    )
    
    if vps[13] == "running" and vps[14]:
        try:
            process = psutil.Process(vps[14])
            cpu_percent = process.cpu_percent(interval=1)
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            memory_percent = (memory_mb / vps[7]) * 100
            
            embed.add_field(
                name="🟢 Status",
                value="```Running```",
                inline=True
            )
            embed.add_field(
                name="⚡ CPU Usage",
                value=f"```{cpu_percent:.1f}%```",
                inline=True
            )
            embed.add_field(
                name="🧠 Memory",
                value=f"```{memory_mb:.0f} MB / {vps[7]} MB ({memory_percent:.1f}%)```",
                inline=True
            )
        except:
            embed.add_field(
                name="⚠️ Status",
                value="```Process not found```",
                inline=False
            )
    else:
        embed.add_field(
            name="🔴 Status",
            value="```Stopped```",
            inline=False
        )
    
    # Disk usage
    if os.path.exists(vps[11]):
        disk_size = os.path.getsize(vps[11]) / 1024 / 1024 / 1024
        embed.add_field(
            name="💾 Disk Usage",
            value=f"```{disk_size:.2f} GB```",
            inline=True
        )
    
    embed.add_field(
        name="🔧 Allocated Resources",
        value=(
            f"```yml\n"
            f"Memory : {vps[7]} MB\n"
            f"CPUs   : {vps[8]} cores\n"
            f"Disk   : {vps[9]}\n"
            f"```"
        ),
        inline=False
    )
    
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="vps_logs", description="📜 View VPS console logs")
@app_commands.describe(vps_id="The ID of the VPS", lines="Number of lines to show (default: 20)")
async def vps_logs_command(interaction: discord.Interaction, vps_id: str, lines: int = 20):
    """View VPS logs"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    logfile = f"{VM_DIR}/{vps_id}.log"
    
    if not os.path.exists(logfile):
        embed = create_info_embed(
            "No Logs Available",
            f"📜 No logs found for VPS **{vps[3]}**\n\n"
            f"Logs are only created when VPS is running without GUI mode."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    try:
        # Read last N lines
        process = await asyncio.create_subprocess_exec(
            'tail', f'-n{lines}', logfile,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        log_content = stdout.decode()
        
        if not log_content.strip():
            log_content = "No logs available yet. VPS may still be booting."
        
        # Truncate if too long
        if len(log_content) > 1900:
            log_content = log_content[-1900:] + "\n... (truncated)"
        
        embed = discord.Embed(
            title=f"📜 VPS Console Logs",
            description=f"**{vps[3]}** (Last {lines} lines)",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(
            name="Console Output",
            value=f"```\n{log_content}\n```",
            inline=False
        )
        
        embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    except Exception as e:
        embed = create_error_embed(
            "Failed to Read Logs",
            f"❌ Could not read logs: {str(e)}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="vps_shell", description="💻 Get shell access command")
@app_commands.describe(vps_id="The ID of the VPS")
async def vps_shell_command(interaction: discord.Interaction, vps_id: str):
    """Get shell access command"""
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if vps[2] != interaction.user.id and not is_admin(interaction.user.id):
        embed = create_error_embed("Access Denied", "🔒 You don't own this VPS!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"💻 VPS Shell Access",
        description=f"**{vps[3]}** (`{vps[1]}`)",
        color=discord.Color.green() if vps[13] == "running" else discord.Color.greyple(),
        timestamp=datetime.now(timezone.utc)
    )
    
    if vps[13] == "running":
        embed.add_field(
            name="🔗 SSH Connection",
            value=f"```bash\nssh -p {vps[6]} {vps[4]}@{DEFAULT_HOSTNAME}```",
            inline=False
        )
        
        embed.add_field(
            name="🔐 Credentials",
            value=(
                f"**Host:** `{DEFAULT_HOSTNAME}`\n"
                f"**Port:** `{vps[6]}`\n"
                f"**Username:** `{vps[4]}`\n"
                f"**Password:** ||`{vps[5]}`||"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📋 One-Line Connection",
            value=f"```bash\nsshpass -p '{vps[5]}' ssh -o StrictHostKeyChecking=no -p {vps[6]} {vps[4]}@{DEFAULT_HOSTNAME}```",
            inline=False
        )
        
        embed.add_field(
            name="💡 Tips",
            value=(
                "• Use `-o StrictHostKeyChecking=no` to skip host key verification\n"
                "• Install `sshpass` for password-less login: `sudo apt install sshpass`\n"
                "• Copy files: `scp -P {port} file.txt {user}@{host}:`"
            ).format(port=vps[6], user=vps[4], host=DEFAULT_HOSTNAME),
            inline=False
        )
    else:
        embed.add_field(
            name="⚠️ VPS Not Running",
            value=f"Please start the VPS first using:\n```/start_vps {vps_id}```",
            inline=False
        )
    
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================
# 👑 ADMIN COMMANDS (continued)
# ============================================

@bot.tree.command(name="admin_list", description="👑 [ADMIN] View all VPS across platform")
async def admin_list_command(interaction: discord.Interaction):
    """List all VPS (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM vps ORDER BY created_at DESC")
    all_vps = cursor.fetchall()
    conn.close()
    
    if not all_vps:
        embed = create_info_embed("No VPS Found", "📦 There are no VPS instances on the platform.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"👑 All VPS Instances",
        description=f"```Total: {len(all_vps)} VPS across platform```",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )
    
    for vps in all_vps[:20]:
        status_emoji = "🟢" if vps[13] == "running" else "🔴"
        try:
            owner = await bot.fetch_user(vps[2])
            owner_name = f"{owner.name}"
        except:
            owner_name = f"ID: {vps[2]}"
        
        value = (
            f"{status_emoji} **Status:** `{vps[13].upper()}`\n"
            f"👤 **Owner:** {owner_name}\n"
            f"💻 **OS:** {OS_IMAGES.get(vps[10], {}).get('name', vps[10])}\n"
            f"🧠 **RAM:** `{vps[7]} MB` | ⚡ **CPU:** `{vps[8]}` | 💾 **Disk:** `{vps[9]}`"
        )
        
        embed.add_field(
            name=f"📦 {vps[3]} (`{vps[1]}`)",
            value=value,
            inline=False
        )
    
    if len(all_vps) > 20:
        embed.set_footer(text=f"Showing 20 of {len(all_vps)} VPS • 🚀 HOPINGBOYZ VPS Manager")
    else:
        embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="admin_stats", description="📊 [ADMIN] View system statistics")
async def admin_stats_command(interaction: discord.Interaction):
    """View system statistics (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM vps")
    total_vps = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM vps WHERE status = 'running'")
    running_vps = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(memory), SUM(cpus) FROM vps")
    resources = cursor.fetchone()
    total_memory = resources[0] or 0
    total_cpus = resources[1] or 0
    
    cursor.execute("SELECT value FROM statistics WHERE key = 'total_vps_created'")
    total_created = cursor.fetchone()[0]
    
    cursor.execute("SELECT value FROM statistics WHERE key = 'total_restarts'")
    total_restarts = cursor.fetchone()[0]
    
    cursor.execute("SELECT value FROM statistics WHERE key = 'total_downloads'")
    total_downloads = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM banned_users")
    banned_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM admins")
    admin_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(DISTINCT owner_id) FROM vps")
    unique_users = cursor.fetchone()[0]
    
    conn.close()
    
    # System resources
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    embed = discord.Embed(
        title="📊 System Statistics Dashboard",
        description="```Real-time platform monitoring```",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )
    
    embed.add_field(
        name="🖥️ VPS Statistics",
        value=(
            f"```yml\n"
            f"Total VPS      : {total_vps}\n"
            f"Running        : {running_vps}\n"
            f"Stopped        : {total_vps - running_vps}\n"
            f"All-Time       : {total_created}\n"
            f"Total Restarts : {total_restarts}\n"
            f"Image Downloads: {total_downloads}\n"
            f"```"
        ),
        inline=True
    )
    
    embed.add_field(
        name="💎 Resource Allocation",
        value=(
            f"```yml\n"
            f"Allocated RAM  : {total_memory} MB\n"
            f"Allocated CPUs : {total_cpus} cores\n"
            f"Active Users   : {unique_users}\n"
            f"Admins         : {admin_count + 1}\n"
            f"Banned Users   : {banned_count}\n"
            f"```"
        ),
        inline=True
    )
    
    embed.add_field(
        name="🖥️ Host System Resources",
        value=(
            f"```yml\n"
            f"CPU Usage      : {cpu_percent}%\n"
            f"RAM Usage      : {memory.percent}%\n"
            f"RAM Available  : {memory.available / 1024 / 1024 / 1024:.1f} GB\n"
            f"Disk Usage     : {disk.percent}%\n"
            f"Disk Free      : {disk.free / 1024 / 1024 / 1024:.1f} GB\n"
            f"```"
        ),
        inline=False
    )
    
    # System info
    embed.add_field(
        name="⚙️ Configuration",
        value=(
            f"```yml\n"
            f"Max VPS/User   : {MAX_VPS_PER_USER}\n"
            f"VM Directory   : {VM_DIR}\n"
            f"Database       : {DB_FILE}\n"
            f"```"
        ),
        inline=True
    )
    
    embed.add_field(
        name="🔐 Security",
        value=(
            f"```yml\n"
            f"Owner ID       : {OWNER_ID}\n"
            f"Admin Role     : {ADMIN_ROLE_ID if ADMIN_ROLE_ID else 'Not Set'}\n"
            f"Total Admins   : {admin_count + 1}\n"
            f"```"
        ),
        inline=True
    )
    
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ban_user", description="🚫 [ADMIN] Ban a user from creating VPS")
@app_commands.describe(user="The user to ban", reason="Reason for ban")
async def ban_user_command(interaction: discord.Interaction, user: discord.User, reason: str = "No reason provided"):
    """Ban a user (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if is_admin(user.id) or (isinstance(user, discord.Member) and has_admin_role(user)):
        embed = create_error_embed("Cannot Ban Admin", "❌ You cannot ban an administrator!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT INTO banned_users (user_id, banned_by, reason) VALUES (?, ?, ?)",
            (user.id, interaction.user.id, reason)
        )
        conn.commit()
        
        embed = create_success_embed(
            "User Banned Successfully!",
            f"🚫 {user.mention} has been banned from creating VPS."
        )
        embed.add_field(name="📝 Reason", value=f"```{reason}```", inline=False)
        embed.add_field(name="👮 Banned By", value=interaction.user.mention, inline=True)
        
        logger.info(f"✅ User {user.id} banned by {interaction.user.id}")
        
    except sqlite3.IntegrityError:
        embed = create_info_embed("Already Banned", f"🚫 {user.mention} is already banned!")
    
    conn.close()
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="unban_user", description="✅ [ADMIN] Unban a user")
@app_commands.describe(user="The user to unban")
async def unban_user_command(interaction: discord.Interaction, user: discord.User):
    """Unban a user (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user.id,))
    
    if cursor.rowcount > 0:
        embed = create_success_embed(
            "User Unbanned Successfully!",
            f"✅ {user.mention} can now create VPS again."
        )
        logger.info(f"✅ User {user.id} unbanned by {interaction.user.id}")
    else:
        embed = create_info_embed("Not Banned", f"ℹ️ {user.mention} is not banned!")
    
    conn.commit()
    conn.close()
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="list_banned", description="📋 [ADMIN] View all banned users")
async def list_banned_command(interaction: discord.Interaction):
    """List all banned users (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM banned_users ORDER BY banned_at DESC")
    banned = cursor.fetchall()
    conn.close()
    
    if not banned:
        embed = create_info_embed("No Banned Users", "✅ There are no banned users!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🚫 Banned Users List",
        description=f"```Total: {len(banned)} banned users```",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    
    for ban in banned[:25]:
        try:
            user = await bot.fetch_user(ban[0])
            user_name = f"{user.name}#{user.discriminator}"
        except:
            user_name = f"Unknown (ID: {ban[0]})"
        
        try:
            banned_by = await bot.fetch_user(ban[1])
            banned_by_name = f"{banned_by.name}"
        except:
            banned_by_name = f"ID: {ban[1]}"
        
        banned_ts = int(datetime.fromisoformat(ban[3]).timestamp())
        
        value = (
            f"👤 **User:** {user_name}\n"
            f"📝 **Reason:** {ban[2]}\n"
            f"👮 **Banned By:** {banned_by_name}\n"
            f"📅 **Date:** <t:{banned_ts}:R>"
        )
        
        embed.add_field(
            name=f"🚫 Ban #{ban[0]}",
            value=value,
            inline=False
        )
    
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="add_admin", description="👑 [ADMIN] Grant admin permissions")
@app_commands.describe(user="The user to make admin")
async def add_admin_command(interaction: discord.Interaction, user: discord.User):
    """Add admin (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT INTO admins (user_id, added_by) VALUES (?, ?)",
            (user.id, interaction.user.id)
        )
        conn.commit()
        
        embed = create_success_embed(
            "Admin Added Successfully!",
            f"👑 {user.mention} is now an administrator!\n\n"
            f"They can now use all admin commands."
        )
        
        logger.info(f"✅ User {user.id} made admin by {interaction.user.id}")
        
    except sqlite3.IntegrityError:
        embed = create_info_embed("Already Admin", f"👑 {user.mention} is already an administrator!")
    
    conn.close()
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remove_admin", description="🔻 [OWNER] Remove admin permissions")
@app_commands.describe(user="The user to remove admin from")
async def remove_admin_command(interaction: discord.Interaction, user: discord.User):
    """Remove admin (owner only)"""
    if not is_owner(interaction.user.id):
        embed = create_error_embed("Access Denied", "❌ Only the bot owner can use this command!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM admins WHERE user_id = ?", (user.id,))
    
    if cursor.rowcount > 0:
        embed = create_success_embed(
            "Admin Removed Successfully!",
            f"🔻 {user.mention} is no longer an administrator."
        )
        logger.info(f"✅ User {user.id} removed as admin by owner")
    else:
        embed = create_info_embed("Not Admin", f"ℹ️ {user.mention} is not an administrator!")
    
    conn.commit()
    conn.close()
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="force_stop", description="⚠️ [ADMIN] Force stop any VPS")
@app_commands.describe(vps_id="The ID of the VPS to force stop")
async def force_stop_command(interaction: discord.Interaction, vps_id: str):
    """Force stop a VPS (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    vps = get_vps_by_id(vps_id)
    
    if not vps:
        embed = create_error_embed("VPS Not Found", f"❌ No VPS found with ID: `{vps_id}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)
    
    success = await stop_vps(vps_id)
    
    if success:
        embed = create_success_embed(
            "VPS Force Stopped!",
            f"⚠️ VPS **{vps[3]}** has been force stopped by admin.\n\n"
            f"Owner: <@{vps[2]}>"
        )
        logger.info(f"✅ VPS {vps_id} force stopped by admin {interaction.user.id}")
    else:
        embed = create_error_embed(
            "Failed to Stop VPS",
            f"❌ Could not stop VPS **{vps[3]}**"
        )
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="cleanup", description="🧹 [ADMIN] Clean orphaned VPS files")
async def cleanup_command(interaction: discord.Interaction):
    """Cleanup orphaned files (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)
    
    # Get all VPS IDs from database
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT vps_id, image_file, seed_file FROM vps")
    db_vps = cursor.fetchall()
    conn.close()
    
    valid_files = set()
    for vps in db_vps:
        valid_files.add(os.path.basename(vps[1]))  # image file
        valid_files.add(os.path.basename(vps[2]))  # seed file
        valid_files.add(f"{vps[0]}.pid")  # pid file
        valid_files.add(f"{vps[0]}.log")  # log file
    
    # Scan VM directory
    cleaned = []
    skipped_cache = []
    
    if os.path.exists(VM_DIR):
        for filename in os.listdir(VM_DIR):
            # Skip cache files (they are intentional)
            if filename.startswith('cache_'):
                skipped_cache.append(filename)
                continue
            
            # Clean orphaned VPS files
            if filename.startswith('vps_') and filename not in valid_files:
                file_path = os.path.join(VM_DIR, filename)
                try:
                    file_size = os.path.getsize(file_path) / 1024 / 1024  # MB
                    os.remove(file_path)
                    cleaned.append(f"{filename} ({file_size:.1f}MB)")
                    logger.info(f"🧹 Cleaned orphaned file: {filename}")
                except Exception as e:
                    logger.error(f"Failed to remove {filename}: {str(e)}")
    
    embed = discord.Embed(
        title="🧹 Cleanup Complete",
        color=discord.Color.green() if cleaned else discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    
    if cleaned:
        total_cleaned = len(cleaned)
        cleaned_list = '\n'.join(cleaned[:15])
        if len(cleaned) > 15:
            cleaned_list += f"\n... and {len(cleaned) - 15} more"
        
        embed.description = f"✅ Removed **{total_cleaned}** orphaned files"
        embed.add_field(
            name="🗑️ Deleted Files",
            value=f"```\n{cleaned_list}\n```",
            inline=False
        )
    else:
        embed.description = "✨ No orphaned files found. System is clean!"
    
    if skipped_cache:
        embed.add_field(
            name="💾 Cache Files (Preserved)",
            value=f"```\n{chr(10).join(skipped_cache[:5])}\n```",
            inline=False
        )
    
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="system_check", description="🔍 [ADMIN] Check system dependencies")
async def system_check_command(interaction: discord.Interaction):
    """Check system requirements (admin only)"""
    if not (is_admin(interaction.user.id) or (isinstance(interaction.user, discord.Member) and has_admin_role(interaction.user))):
        embed = create_error_embed("Access Denied", "❌ This command requires admin permissions!")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer(thinking=True)
    
    checks = {}
    
    # Check QEMU
    try:
        process = await asyncio.create_subprocess_exec(
            'which', 'qemu-system-x86_64',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            qemu_path = stdout.decode().strip()
            # Get version
            process = await asyncio.create_subprocess_exec(
                'qemu-system-x86_64', '--version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            version = stdout.decode().split('\n')[0]
            checks['qemu'] = {'status': '✅', 'info': f"{version}\nPath: {qemu_path}"}
        else:
            checks['qemu'] = {'status': '❌', 'info': 'Not installed'}
    except Exception as e:
        checks['qemu'] = {'status': '❌', 'info': str(e)}
    
    # Check cloud-localds
    try:
        process = await asyncio.create_subprocess_exec(
            'which', 'cloud-localds',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            checks['cloud-localds'] = {'status': '✅', 'info': stdout.decode().strip()}
        else:
            checks['cloud-localds'] = {'status': '❌', 'info': 'Not installed'}
    except Exception as e:
        checks['cloud-localds'] = {'status': '❌', 'info': str(e)}
    
    # Check qemu-img
    try:
        process = await asyncio.create_subprocess_exec(
            'which', 'qemu-img',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            checks['qemu-img'] = {'status': '✅', 'info': stdout.decode().strip()}
        else:
            checks['qemu-img'] = {'status': '❌', 'info': 'Not installed'}
    except Exception as e:
        checks['qemu-img'] = {'status': '❌', 'info': str(e)}
    
    # Check KVM support
    try:
        if os.path.exists('/dev/kvm'):
            checks['kvm'] = {'status': '✅', 'info': '/dev/kvm exists'}
        else:
            checks['kvm'] = {'status': '⚠️', 'info': '/dev/kvm not found (will use TCG - slower)'}
    except Exception as e:
        checks['kvm'] = {'status': '❌', 'info': str(e)}
    
    # Check VM directory
    try:
        if os.path.exists(VM_DIR):
            if os.access(VM_DIR, os.W_OK):
                disk_usage = psutil.disk_usage(VM_DIR)
                free_gb = disk_usage.free / 1024 / 1024 / 1024
                checks['vm_dir'] = {'status': '✅', 'info': f"Writable, {free_gb:.1f}GB free"}
            else:
                checks['vm_dir'] = {'status': '❌', 'info': 'Not writable'}
        else:
            checks['vm_dir'] = {'status': '⚠️', 'info': 'Does not exist (will be created)'}
    except Exception as e:
        checks['vm_dir'] = {'status': '❌', 'info': str(e)}
    
    # Check Python packages
    try:
        import aiohttp
        import aiofiles
        import psutil
        from dotenv import load_dotenv
        checks['python_deps'] = {'status': '✅', 'info': 'All packages installed'}
    except ImportError as e:
        checks['python_deps'] = {'status': '❌', 'info': f'Missing: {str(e)}'}
    
    # Build embed
    embed = discord.Embed(
        title="🔍 System Dependency Check",
        description="Checking VPS Manager requirements...",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )
    
    all_good = all(check['status'] == '✅' for check in checks.values())
    
    for name, check in checks.items():
        embed.add_field(
            name=f"{check['status']} {name.upper().replace('_', ' ')}",
            value=f"```{check['info']}```",
            inline=False
        )
    
    # Installation help
    if not all_good:
        embed.add_field(
            name="📦 Installation Commands",
            value=(
                "```bash\n"
                "# Ubuntu/Debian\n"
                "sudo apt update\n"
                "sudo apt install qemu-system-x86-64 cloud-image-utils\n\n"
                "# Python packages\n"
                "pip install -r requirements.txt\n"
                "```"
            ),
            inline=False
        )
    
    if all_good:
        embed.color = discord.Color.green()
        embed.description = "✅ All dependencies are installed and working!"
    else:
        embed.color = discord.Color.orange()
        embed.description = "⚠️ Some dependencies are missing or not working properly"
    
    embed.set_footer(text="🚀 HOPINGBOYZ VPS Manager")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ============================================
# 🚀 BOT STARTUP
# ============================================

def main():
    """Main bot startup function"""
    try:
        logger.info("🚀 Starting HOPINGBOYZ VPS Manager Bot...")
        bot.run(BOT_TOKEN, log_handler=None)
    except discord.LoginFailure:
        logger.error("❌ Invalid bot token! Check your .env file.")
    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")

if __name__ == "__main__":
    main()
