# 🚀 Joy VPS Manager Bot

Professional Discord bot for managing Virtual Private Servers (VPS) using QEMU/KVM with async operations.

## ✨ Features

### 🎯 Core Features
- **8 Linux Distributions**: Ubuntu 22.04/24.04, Debian 11/12, Fedora 40, CentOS Stream 9, AlmaLinux 9, Rocky Linux 9
- **Async Operations**: Non-blocking downloads and operations
- **Resource Monitoring**: Real-time CPU, RAM, and disk usage tracking
- **Auto Status Updates**: Bot status shows VPS count every 30 seconds
- **Secure Passwords**: 16-character random password generation
- **Image Caching**: Download OS images once, reuse for all VPS

### 👤 User Commands
- `/create_vps` - Create new VPS with custom specs
- `/list` - View all your VPS instances
- `/start_vps` - Boot up a VPS
- `/stop_vps` - Shut down a VPS
- `/restart_vps` - Reboot a VPS
- `/vps_info` - Get detailed VPS information
- `/vps_stats` - View resource usage
- `/change_pass` - Generate new SSH password
- `/delete_vps` - Remove a VPS permanently

### 👑 Admin Commands
- `/admin_list` - View all VPS platform-wide
- `/admin_stats` - System statistics dashboard
- `/ban_user` - Ban user from creating VPS
- `/unban_user` - Unban a user
- `/list_banned` - View banned users
- `/add_admin` - Grant admin permissions
- `/remove_admin` - Revoke admin permissions (owner only)
- `/force_stop` - Force stop any VPS
- `/cleanup` - Remove orphaned files

## 📋 Requirements

### System Requirements
- Linux system (Ubuntu/Debian recommended)
- Python 3.10 or higher
- QEMU/KVM with hardware virtualization support
- Minimum 4GB RAM (8GB+ recommended)
- 50GB+ free disk space

### System Packages
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install qemu-system-x86-64 cloud-image-utils python3-pip

# Check KVM support
egrep -c '(vmx|svm)' /proc/cpuinfo
# Should return > 0
```

### Python Packages
```bash
pip install -r requirements.txt
```

## 🚀 Installation

### 1. Clone Repository
```bash
git clone https://github.com/yourusername/vps-manager-bot.git
cd vps-manager-bot
```

### 2. Install Dependencies
```bash
# System packages
sudo apt install qemu-system-x86-64 cloud-image-utils

# Python packages
pip install -r requirements.txt
```

### 3. Configure Environment
```bash
# Copy example config
cp .env.example .env

# Edit configuration
nano .env
```

**Required configuration:**
```bash
BOT_TOKEN=your_discord_bot_token_here
OWNER_ID=your_discord_user_id_here
```

**Optional configuration:**
```bash
ADMIN_ROLE_ID=0                    # Discord role ID for admins
DEFAULT_HOSTNAME=localhost          # SSH hostname
MAX_VPS_PER_USER=5                 # VPS limit per user
VM_DIR=/home/username/vms          # VPS storage directory
```

### 4. Create Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application"
3. Go to "Bot" section
4. Click "Reset Token" and copy the token
5. Enable these Privileged Gateway Intents:
   - Server Members Intent
   - Message Content Intent
6. Go to OAuth2 > URL Generator
7. Select scopes: `bot`, `applications.commands`
8. Select permissions: `Administrator` (or specific permissions)
9. Copy the generated URL and invite bot to your server

### 5. Get Your User ID

1. Enable Developer Mode in Discord:
   - Settings > Advanced > Developer Mode
2. Right-click your profile > Copy User ID
3. Paste in `.env` as `OWNER_ID`

### 6. Run the Bot
```bash
python vps_bot.py
```

## 📊 Configuration Details

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ Yes | - | Discord bot token |
| `OWNER_ID` | ✅ Yes | 0 | Bot owner's Discord user ID |
| `ADMIN_ROLE_ID` | ❌ No | 0 | Discord role ID for admins |
| `DEFAULT_HOSTNAME` | ❌ No | localhost | SSH connection hostname |
| `MAX_VPS_PER_USER` | ❌ No | 5 | Max VPS per user (admins bypass) |
| `VM_DIR` | ❌ No | ~/vms | Directory for VPS storage |

### VPS Limits

| Resource | Minimum | Maximum | Default |
|----------|---------|---------|---------|
| Memory | 512 MB | 8192 MB | 2048 MB |
| CPUs | 1 | 8 | 2 |
| Disk | 10G | 100G | 20G |
| SSH Port | 2222 | 65535 | Auto |

## 🔐 Security Features

### User Management
- **Ban System**: Prevent abusive users from creating VPS
- **Admin System**: Database-stored admin list
- **Role-Based Access**: Optional Discord role admin support
- **VPS Limits**: Configurable per-user VPS limits

### Password Security
- **Random Generation**: 16-character secure passwords
- **Special Characters**: Includes letters, numbers, and symbols
- **Password Changes**: Users can regenerate passwords anytime

### Access Control
- Users can only manage their own VPS
- Admins can manage all VPS
- Owner has full control

## 📁 File Structure

```
vps-manager-bot/
├── vps_bot.py              # Main bot file
├── requirements.txt        # Python dependencies
├── .env.example           # Example configuration
├── .env                   # Your configuration (create this)
├── README.md              # This file
├── vps_manager.db         # SQLite database (auto-created)
├── vps_manager.log        # Bot logs (auto-created)
└── ~/vms/                 # VPS storage directory
    ├── cache_ubuntu22.img # Cached OS images
    ├── vps_xxxxx.img      # VPS disk images
    ├── vps_xxxxx-seed.iso # Cloud-init configs
    └── vps_xxxxx.pid      # Process IDs
```

## 🛠️ Troubleshooting

### Bot won't start
```bash
# Check Python version
python3 --version  # Should be 3.10+

# Check dependencies
pip install -r requirements.txt

# Check token
cat .env | grep BOT_TOKEN

# Check logs
tail -f vps_manager.log
```

### VPS creation fails
```bash
# Check QEMU installation
which qemu-system-x86_64

# Check KVM support
lsmod | grep kvm
ls -la /dev/kvm

# Check disk space
df -h ~/vms

# Check permissions
ls -la ~/vms

# Use system check command
/system_check
```

### VPS won't start (PID file error)
```bash
# Check if QEMU is installed correctly
qemu-system-x86_64 --version

# Check if VPS files exist
ls -la ~/vms/vps_*.img

# Check for running QEMU processes
ps aux | grep qemu

# Try manual start for debugging
cd ~/vms
qemu-system-x86_64 -enable-kvm -m 2048 -smp 2 \
  -drive file=vps_xxxxx.img,format=qcow2 \
  -drive file=vps_xxxxx-seed.iso,format=raw \
  -netdev user,id=net0,hostfwd=tcp::2222-:22 \
  -device virtio-net-pci,netdev=net0 -nographic

# Check VPS logs
/vps_logs vps_id:your_vps_id
```

### Disk resize warnings
The bot now automatically prevents shrinking operations. If you see warnings:
- The cached image is 600MB+
- Requesting disk smaller than 600MB causes warnings
- Solution: Use minimum 1G disk size
- Cache files are preserved and reused

### Heartbeat warnings
- These occur when downloading large OS images (600MB+)
- Bot automatically reconnects
- Operations continue in background
- **Fixed in this version** with async downloads!
- Images are cached - subsequent VPS creation is instant

### Connection issues
```bash
# Check if VPS is running
ps aux | grep qemu | grep vps_id

# Check SSH port
netstat -tlnp | grep <port>
ss -tlnp | grep <port>

# Test SSH connection
ssh -vvv -p <port> username@localhost

# Check firewall
sudo ufw status
sudo iptables -L -n
```

### Cache file management
```bash
# List cache files
ls -lh ~/vms/cache_*.img

# Remove cache to force re-download
rm ~/vms/cache_*.img

# Clean orphaned files (preserves cache)
/cleanup
```

## 📝 Usage Examples

### Creating a VPS
```
/create_vps memory:2048 cpus:2 disk:20G os_type:Ubuntu 22.04 LTS
```

### Starting a VPS
```
/start_vps vps_id:vps_abc123def456
```

### Viewing VPS Info
```
/vps_info vps_id:vps_abc123def456
```

### Admin Stats
```
/admin_stats
```

## 🤝 Support

### Common Issues
1. **Heartbeat blocked**: Normal during large downloads, auto-recovers
2. **qemu-img not found**: Install qemu-system package
3. **Permission denied**: Check VM_DIR permissions
4. **Port already in use**: VPS auto-finds free ports

### Getting Help
- Check bot logs: `vps_manager.log`
- Check system logs: `journalctl -xe`
- Discord: Join our support server
- GitHub: Open an issue

## 📄 License

MIT License - Feel free to modify and distribute

## 🎉 Credits

**HOPINGBOYZ VPS Manager**  
Professional VPS management for Discord

---

Made with ❤️ by HOPINGBOYZ
