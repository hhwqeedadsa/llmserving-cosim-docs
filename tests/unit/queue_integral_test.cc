#include "queue_integral.hh"
#include <stdexcept>
int main() {
  moe_cosim::QueueIntegral q;
  q.Change(10,100);q.Change(15,300);q.Change(15,200);q.Change(30,0);
  if(q.Area(40)!=3500 || q.Bytes()!=0 || q.Peak()!=300 || q.Events()!=4)
    throw std::runtime_error("Queue integral lost simultaneous transitions");
  q.Change(45,100);if(q.Area(50)!=4000)throw std::runtime_error("Open queue tail was omitted");
  bool rejected=false;try{q.Change(44,0);}catch(const std::exception&){rejected=true;}
  if(!rejected)throw std::runtime_error("Backwards queue clock accepted");
}
