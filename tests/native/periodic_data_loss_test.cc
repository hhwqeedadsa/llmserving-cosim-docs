#include "backend/periodic_data_loss.hh"
#include <cassert>
#include <iostream>
int main() {
  unsigned checks=0;
  for(unsigned denominator=1;denominator<=16;++denominator)
    for(unsigned numerator=1;numerator<=denominator;++numerator)
      for(unsigned offset=0;offset<denominator;++offset) {
        moe_cosim::PeriodicDataLoss loss(numerator,denominator,offset,10,10+denominator*4);
        assert(!loss.Arrival(9));++checks;
        unsigned accepted=0;
        for(unsigned i=0;i<denominator*4;++i)accepted+=!loss.Arrival(10+i);
        assert(accepted==4*(denominator-numerator));assert(loss.Dropped()==4*numerator);
        assert(!loss.Arrival(10+denominator*4));assert(loss.Window()==4*denominator);
        assert(loss.Total()==4*denominator+2);checks+=5;
      }
  for(unsigned numerator: {0u,3u}) {
    bool refused=false;try {moe_cosim::PeriodicDataLoss bad(numerator,2,0,0,10);}catch(const std::invalid_argument&){refused=true;}
    assert(refused);++checks;
  }
  std::cout<<checks<<" independent periodic/window assertions passed\n";
}
