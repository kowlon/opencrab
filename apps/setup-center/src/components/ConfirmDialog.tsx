import { useTranslation } from "react-i18next";
import { ModalOverlay } from "./ModalOverlay";

type ConfirmDialogProps = {
  dialog: { message: string; onConfirm: () => void } | null;
  onClose: () => void;
};

export function ConfirmDialog({ dialog, onClose }: ConfirmDialogProps) {
  const { t } = useTranslation();
  if (!dialog) return null;
  return (
    <ModalOverlay onClose={onClose}>
      <div className="modalContent" style={{ maxWidth: 380, padding: 24 }}>
        <div style={{ fontSize: 14, lineHeight: 1.6, marginBottom: 20 }}>{dialog.message}</div>
        <div className="dialogFooter" style={{ justifyContent: "flex-end" }}>
          <button className="btnSmall" onClick={onClose}>{t("common.cancel")}</button>
          <button className="btnSmall" style={{ background: "var(--danger, #e53935)", color: "#fff", border: "none" }} onClick={() => { dialog.onConfirm(); onClose(); }}>{t("common.confirm")}</button>
        </div>
      </div>
    </ModalOverlay>
  );
}
