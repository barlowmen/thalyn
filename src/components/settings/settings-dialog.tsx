import { ProviderSection } from "@/components/settings/provider-section";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

export function SettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <header className="space-y-1">
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            API keys, providers, and the like. v0.3 ships a single
            section; more panels arrive as the surfaces come online.
          </DialogDescription>
        </header>

        <div className="mt-2 max-h-[60vh] overflow-y-auto pr-1">
          <ProviderSection />
        </div>
      </DialogContent>
    </Dialog>
  );
}
