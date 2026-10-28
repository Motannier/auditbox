import React, { useEffect, useRef } from "react";
import { Command, History, ShieldAlert, Sparkles } from "lucide-react";
import { deviceProfileCompletionLabel } from "../../lib/deviceProfiles.js";

function isRecommendation(item) {
  return item.source === "factory" || item.source === "prediction" || item.source === "markov";
}

export function CommandSuggestions({
  suggestions,
  selectedIndex,
  placement,
  deviceProfile,
  onSelect,
  onApply,
}) {
  const mouseMotionRef = useRef(null);

  useEffect(() => {
    // Track coordinates even while the popup is hidden. Opening or scrolling
    // it under a stationary mouse must not replace the keyboard selection.
    const trackMouseMotion = (event) => {
      const previous = mouseMotionRef.current;
      mouseMotionRef.current = {
        event,
        x: event.clientX,
        y: event.clientY,
        moved: previous
          ? event.clientX !== previous.x || event.clientY !== previous.y
          : Boolean(event.movementX || event.movementY),
      };
    };
    window.addEventListener("mousemove", trackMouseMotion, { capture: true, passive: true });
    return () => window.removeEventListener("mousemove", trackMouseMotion, true);
  }, []);

  if (!suggestions.length) return null;
  const recommendationMode = suggestions.some(isRecommendation);
  const catalogLabel = deviceProfileCompletionLabel(deviceProfile);
  return (
    <div className="command-suggestions" style={placement || undefined} role="listbox" aria-label="命令补全与推荐">
      <div className="command-suggestions-header">
        <strong>{recommendationMode ? "智能推荐" : "命令补全"}</strong>
        <span><kbd>↑</kbd><kbd>↓</kbd> 选择　<kbd>Enter</kbd>/<kbd>Tab</kbd> 填入　<kbd>Esc</kbd> 关闭</span>
      </div>
      <div className="command-suggestions-list">
        {suggestions.map((item, index) => {
          const recommendation = isRecommendation(item);
          return (
            <button
              key={`${item.command}-${item.last_used_at || index}`}
              type="button"
              className={index === selectedIndex ? "active" : ""}
              onMouseMove={(event) => {
                const motion = mouseMotionRef.current;
                if (motion?.event === event.nativeEvent && motion.moved && index !== selectedIndex) {
                  onSelect(index);
                }
              }}
              onMouseDown={(event) => {
                event.preventDefault();
                onApply(index);
              }}
              role="option"
              title={[item.models && `适用范围：${item.models}`, item.risk_reason].filter(Boolean).join("\n") || undefined}
              aria-selected={index === selectedIndex}
            >
              {recommendation ? <Sparkles size={16} /> : item.source === "catalog" ? <Command size={16} /> : <History size={16} />}
              <span className="command-suggestion-body">
                <b>{item.command}</b>
                <small>
                  {recommendation
                    ? [item.behavior_label, item.reason, item.view && `视图：${item.view}`].filter(Boolean).join(" · ") || "建议的下一步检查"
                    : item.source === "catalog"
                      ? [catalogLabel, item.behavior_label, item.view && `视图：${item.view}`].filter(Boolean).join(" · ")
                      : `当前设备使用 ${item.usage_count || 1} 次`}
                  {item.risk === "confirm" ? <em><ShieldAlert size={12} /> 需二次确认</em> : null}
                  {item.models ? <span> · 适用型号见悬停说明</span> : null}
                </small>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
