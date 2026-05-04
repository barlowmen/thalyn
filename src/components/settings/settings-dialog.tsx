import { EmailSection } from "@/components/settings/email-section";
import { ObservabilitySection } from "@/components/settings/observability-section";
import { ProviderSection } from "@/components/settings/provider-section";
import { VoiceSection } from "@/components/settings/voice-section";
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
            Providers, API keys, and the user-controlled telemetry
            destinations. Anything Thalyn ever sends out the
            network leaves through one of these settings.
          </DialogDescription>
        </header>

        <div className="mt-2 max-h-[60vh] space-y-6 overflow-y-auto pr-1">
          <ProviderSection />
          <VoiceSection />
          <EmailSection />
          <ObservabilitySection />
        </div>
      </DialogContent>
    </Dialog>
  );
}
