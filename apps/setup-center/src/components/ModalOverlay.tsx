import { useRef, useCallback, type ReactNode, type CSSProperties, type MouseEvent } from "react";

export function ModalOverlay({
  children,
  onClose,
  className = "modalOverlay",
  style,
}: {
  children: ReactNode;
  onClose: () => void;
  className?: string;
  style?: CSSProperties;
}) {
  const mouseDownOnOverlay = useRef(false);

  const handleMouseDown = useCallback((e: MouseEvent) => {
    mouseDownOnOverlay.current = e.target === e.currentTarget;
  }, []);

  const handleMouseUp = useCallback((e: MouseEvent) => {
    if (e.target === e.currentTarget && mouseDownOnOverlay.current) onClose();
    mouseDownOnOverlay.current = false;
  }, [onClose]);

  return (
    <div className={className} style={style} onMouseDown={handleMouseDown} onMouseUp={handleMouseUp}>
      {children}
    </div>
  );
}
