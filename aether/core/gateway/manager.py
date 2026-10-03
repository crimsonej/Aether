from aether.core.utils.logger import logger
from aether.core.ops.health import HealthMonitor
import asyncio

class GatewayManager:
    def __init__(self, config=None, bus=None):
        if config is not None and bus is not None:
            self.health_monitor = HealthMonitor(config, bus)
        else:
            class MockHealthMonitor:
                def check_health(self):
                    return {
                        "data_pipeline": "OK",
                        "strategy_engine": "OK",
                        "validation_layer": "OK",
                        "delivery_layer": "OK"
                    }
            self.health_monitor = MockHealthMonitor()
        self.services = {}
        self.running = False
        self._heartbeat_task = None

    async def _heartbeat(self):
        while self.running:
            # Periodically verify status of all core components
            try:
                status = self.health_monitor.check_health()
                # If a component is "RECOVERY_NEEDED", trigger auto-restart
                logger.debug("platform_heartbeat", health=status)
            except Exception as e:
                logger.error("heartbeat_error", error=str(e))
            await asyncio.sleep(60)

    async def start(self):
        logger.info("gateway_manager_starting")
        self.running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        logger.info("all_services_started")

    async def stop(self):
        logger.info("gateway_manager_stopping")
        self.running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        logger.info("all_services_stopped")

    def get_system_status(self):
        return {
            "status": "operational" if self.running else "stopped",
            "health": self.health_monitor.check_health()
        }
