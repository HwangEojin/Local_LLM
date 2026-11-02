import { AuthGate } from "@/components/AuthGate";
import { KillSwitchBanner, Sidebar } from "@/components/Sidebar";

// 로그인 뒤의 모든 화면이 공유하는 레이아웃: 사이드바(작업/시스템) + 중지 스위치 배너.
// 각 page.tsx 는 더 이상 <AuthGate>/<Header> 를 직접 두지 않는다(UPGRADE_PLAN.md B1).
export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <KillSwitchBanner />
          {children}
        </div>
      </div>
    </AuthGate>
  );
}
