export const metadata = { title: "DisputeDesk", description: "PayPal dispute evidence agent" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (<html lang="en"><body style={{fontFamily:"Inter,system-ui",margin:0,background:"#0b0e14",color:"#e6e9f0"}}>{children}</body></html>);
}
