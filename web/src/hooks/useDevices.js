import { useCallback, useEffect, useRef, useState } from "react";
import {
  createDevice,
  fetchDevices,
  fetchDeviceStatuses,
  updateDevice,
} from "../services/deviceService.js";

const STATUS_POLL_INTERVAL_MS = 2_000;

export function useDevices(search = "") {
  const [targets, setTargets] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const failureCountsRef = useRef(new Map());

  const reloadTargets = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchDevices({ search });
      setTargets((current) =>
        data.items.map((item) => {
          const previous = current.find((target) => target.id === item.id);
          return { ...item, status: previous?.status || "probing" };
        }),
      );
      setTotal(data.total);
      return data.items;
    } catch (err) {
      setError(err.message || "设备列表加载失败");
      return [];
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      if (!cancelled) await reloadTargets();
    }, 150);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [reloadTargets]);

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    async function probe() {
      try {
        const statuses = await fetchDeviceStatuses();
        if (cancelled) return;
        const byId = new Map(statuses.map((item) => [item.id, item.status]));
        setTargets((current) =>
          current.map((target) => {
            const next = byId.get(target.id);
            if (next === "online") {
              failureCountsRef.current.set(target.id, 0);
              return { ...target, status: "online" };
            }
            if (next === "offline") {
              const failures = (failureCountsRef.current.get(target.id) || 0) + 1;
              failureCountsRef.current.set(target.id, failures);
              if (failures >= 2 || target.status === "offline") {
                return { ...target, status: "offline" };
              }
            }
            return target;
          }),
        );
      } catch {
        // 单次状态请求失败不代表所有设备都已离线。
      } finally {
        if (!cancelled) timer = window.setTimeout(probe, STATUS_POLL_INTERVAL_MS);
      }
    }

    timer = window.setTimeout(probe, 250);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  const saveDevice = useCallback(
    async (payload) => {
      const saved = await createDevice(payload);
      failureCountsRef.current.set(saved.id, 0);
      await reloadTargets();
      return saved;
    },
    [reloadTargets],
  );

  const editDevice = useCallback(
    async (deviceId, payload) => {
      const updated = await updateDevice(deviceId, payload);
      failureCountsRef.current.set(deviceId, 0);
      await reloadTargets();
      return updated;
    },
    [reloadTargets],
  );

  return {
    targets,
    total,
    loading,
    error,
    reloadTargets,
    saveDevice,
    editDevice,
  };
}
