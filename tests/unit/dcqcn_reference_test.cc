#include "dcqcn_reference.hh"
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace moe_rdma;
static unsigned assertions=0;
static void check(bool value) { ++assertions;if(!value)throw std::runtime_error("reference state oracle mismatch at assertion "+std::to_string(assertions)); }
static void near(double a,double b) { check(std::abs(a-b)<1e-12); }
int main() {
  // Hand enumerated NP boundaries, including late pending feedback after data.
  DcqcnNp np(50);check(np.Mark(0));check(!np.Mark(1));check(!np.Mark(49));
  check(np.Pending());check(np.Expire(50));check(!np.Pending());
  check(!np.Expire(100));check(np.Deadline()==-1);check(np.Mark(130));
  check(!np.Expire(180));check(np.Mark(180));check(!np.Mark(180));
  // A mark exactly on a pending deadline cannot erase the old interval's
  // notification or be counted twice. Test both callback insertion orders.
  DcqcnNp before(50),after(50);
  for(auto* n:{&before,&after}) { check(n->Mark(0));check(!n->Mark(49)); }
  check(before.Mark(50));check(before.Pending());
  check(after.Expire(50));check(!after.Mark(50));check(after.Pending());
  check(before.Expire(100));check(after.Expire(100));
  // Empty interval boundary with a new mark, both callback orders.
  DcqcnNp a(50),b(50);check(a.Mark(0));check(b.Mark(0));
  check(a.Mark(50));check(!b.Expire(50));check(b.Mark(50));
  check(a.Deadline()==b.Deadline() && !a.Pending() && !b.Pending());

  DcqcnRpConfig cfg{55,20,1000,100,100,200,2,0.25};
  DcqcnRp rp(cfg,1000);
  check(rp.Sent(10000)==0 && !rp.Active());check(rp.Rate()==1000);
  rp.Cnp(10);check(rp.Rate()==500 && rp.Target()==1000);near(rp.AlphaValue(),1);
  check(rp.AlphaDue()==65 && rp.RecoveryDue()==30);
  check(rp.Sent(999)==0 && rp.Remaining()==1);
  check(rp.Sent(1)==1 && rp.ByteRounds()==1 && rp.TimerRounds()==0 && rp.Rate()==750);
  rp.Timer(30);check(rp.TimerRounds()==1 && rp.ByteRounds()==1 && rp.Rate()==875);
  check(rp.Sent(1000)==1 && rp.Rate()==937); // max==F: additive, clamped target
  rp.Alpha(65);near(rp.AlphaValue(),0.75);
  rp.Cnp(70); // floor(937*(1-.75/2))=585; update alpha=.8125
  check(rp.Rate()==585 && rp.Target()==937);near(rp.AlphaValue(),0.8125);
  check(rp.TimerRounds()==0 && rp.ByteRounds()==0 && rp.Remaining()==1000);
  check(rp.AlphaDue()==125 && rp.RecoveryDue()==90);
  // Fast/additive/hyper decisions need BOTH independent trigger counts.
  rp.Timer(90);check(rp.Rate()==761 && rp.Target()==937);
  rp.Timer(110);check(rp.Rate()==880 && rp.Target()==1000);
  check(rp.Sent(3000)==3 && rp.ByteRounds()==3 && rp.TimerRounds()==2);
  rp.Timer(130);check(rp.TimerRounds()==3 && rp.ByteRounds()==3);
  check(rp.Rate()<=1000 && rp.Rate()>=100);
  // With room below line rate, strict Fig7 gates distinguish both paths.
  DcqcnRp slow({55,20,1000,10,10,50,2,0.25},1000);
  for(int i=0;i<4;++i)slow.Cnp(i); // 1000 -> 500 -> 250 -> 125 -> 62, target125
  check(slow.Rate()==62 && slow.Target()==125);
  slow.Timer(23);check(slow.Rate()==93 && slow.Target()==125);
  slow.Timer(43);check(slow.Rate()==114 && slow.Target()==135);
  slow.Sent(1000);check(slow.Target()==145 && slow.Rate()==129);
  slow.Sent(1000);check(slow.Target()==155 && slow.Rate()==142);
  slow.Sent(1000);check(slow.Target()==165 && slow.Rate()==153);
  slow.Timer(63);check(slow.Target()==215 && slow.Rate()==184);
  for(int i=0;i<100;++i) { slow.Cnp(1000+i);check(slow.Rate()>=10); }
  // Independent closed-form byte accounting: every threshold is based on
  // sent bytes since the last CNP, not the application's offered total.
  for(uint64_t left:{1,999,1000,1001,8000}) {
    DcqcnRp z(cfg,1000);z.Cnp(0);uint64_t total=0,events=0;
    for(int j=0;j<17;++j) {
      events+=z.Sent(left);total+=left;
      check(events==total/1000);check(z.Remaining()==1000-total%1000);
    }
  }
  bool rejected=false;try { DcqcnRp bad({0,1,1,1,1,1,1,.5},100); }catch(const std::invalid_argument&) { rejected=true; }
  check(rejected);
  std::cout<<assertions<<" reference state assertions passed; no network or hardware calibration\n";
}
