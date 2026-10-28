import { useState } from "react";
import { Monitor, Smartphone, Tablet, CircleHelp } from "lucide-react";
import { detectClientDevice } from "../../lib/clientDevice.js";

const DEVICE_ICONS = { computer: Monitor, phone: Smartphone, tablet: Tablet, unknown: CircleHelp };

export function ClientDeviceLabel({ className = "", showIcon = false }) {
  const [device] = useState(() => detectClientDevice());
  const Icon = DEVICE_ICONS[device.deviceType];
  return (
    <span className={`client-device-label ${className}`} title="根据当前浏览器识别接入设备类型">
      {showIcon ? <Icon size={20} aria-hidden="true" /> : null}
      {`接入设备：${device.label}`}
    </span>
  );
}
