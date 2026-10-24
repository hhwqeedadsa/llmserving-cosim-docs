/* Recording substitutes for selected Linux v6.12 functions, not a driver.
 * Single-threaded references, timers, page copy and firmware wait are replaced.
 * No actual GPU, DMA, RDMA, kernel workqueue or hardware completion is executed.
 */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define unlikely(x) (x)
#define rxe_dbg_mr(...) ((void)0)
#define rxe_dbg_qp(...) ((void)0)
#define RXE_POOL_TIMEOUT 200
static int warnings,cleanup_errors;
#define WARN_ON(x) ((x)?(++warnings,1):0)
#define rxe_err_mr(...) ((void)++cleanup_errors)
struct completion {bool done;};
struct kref {int refs;};
struct atomic_value {int value;};
struct rxe_pool_elem;
struct xarray {struct rxe_pool_elem *entry;};
struct rxe_pool {struct xarray xa;void (*cleanup)(struct rxe_pool_elem *);struct atomic_value num_elem;};
struct rxe_pool_elem {struct rxe_pool *pool;void *obj;struct kref ref_cnt;struct completion complete;u32 index;};
struct rxe_dev {struct rxe_pool mr_pool;};
struct ib_pd {void *device;};
struct rxe_pd {struct ib_pd ibpd;};
struct ib_mr {void *device;struct ib_pd *pd;int type;u64 iova,length;};
struct rxe_mr {struct ib_mr ibmr;struct rxe_pool_elem elem;u32 lkey,rkey;int access,state;struct atomic_value num_mw;u8 data[16];};
struct ib_qp {void *device;struct ib_pd *pd;};
struct rxe_qp {struct ib_qp ibqp;};
struct ib_udata {int unused;};
struct rxe_send_wqe {struct {struct {struct {struct ib_mr *mr;u32 key,access;} reg;} wr;} wr;};
enum rxe_mr_lookup_type {RXE_LOOKUP_LOCAL,RXE_LOOKUP_REMOTE};
enum rxe_mr_copy_dir {RXE_TO_MR_OBJ,RXE_FROM_MR_OBJ};
enum {IB_MR_TYPE_DMA,IB_MR_TYPE_USER,IB_MR_TYPE_MEM_REG};
enum {RXE_MR_STATE_FREE,RXE_MR_STATE_VALID,RXE_MR_STATE_INVALID};
enum {IB_ACCESS_REMOTE_READ=1,IB_ACCESS_REMOTE_WRITE=2,RXE_ACCESS_REMOTE=3};
#define to_rdev(p) ((struct rxe_dev *)(p))
#define to_rmr(p) container_of(p,struct rxe_mr,ibmr)
#define mr_pd(p) container_of((p)->ibmr.pd,struct rxe_pd,ibpd)
static int atomic_read(struct atomic_value *p){return p->value;}
static void atomic_dec(struct atomic_value *p){--p->value;}
static void rcu_read_lock(void){}
static void rcu_read_unlock(void){}
static struct rxe_pool_elem *xa_load(struct xarray *p,u32 index){return p->entry && p->entry->index==index?p->entry:NULL;}
static void *xa_erase(struct xarray *p,u32 index){void *old=xa_load(p,index);if(old)p->entry=NULL;return old;}
static int xa_err(void *p){(void)p;return 0;}
static int kref_get_unless_zero(struct kref *p){if(!p->refs)return 0;++p->refs;return 1;}
static int kref_put(struct kref *p,void (*release)(struct kref *)){assert(p->refs>0);if(!--p->refs){release(p);return 1;}return 0;}
static void complete(struct completion *p){p->done=true;}
static bool completion_done(struct completion *p){return p->done;}
static void might_sleep(void){}
static unsigned long jiffies;
static bool time_before(unsigned long a,unsigned long b){return a<b;}
static void mdelay(int ms){jiffies+=(unsigned)ms;}
int __rxe_put(struct rxe_pool_elem *elem);
#define rxe_put(p) __rxe_put(&(p)->elem)
int __rxe_cleanup(struct rxe_pool_elem *elem,bool sleepable);
#define rxe_cleanup(p) __rxe_cleanup(&(p)->elem,true)
void *rxe_pool_get_index(struct rxe_pool *pool,u32 index);
struct rxe_mr *lookup_mr(struct rxe_pd *,int,u32,enum rxe_mr_lookup_type);
int rxe_mr_copy(struct rxe_mr *,u64,void *,unsigned,enum rxe_mr_copy_dir);
static u8 random_values[2];static unsigned random_index;
static void get_random_bytes(void *p,size_t n){assert(n==1 && random_index<2);*(u8 *)p=random_values[random_index++];}
static int rxe_mr_copy_xarray(struct rxe_mr *mr,u64 va,void *addr,unsigned length,enum rxe_mr_copy_dir dir){
    assert(va>=mr->ibmr.iova && va-mr->ibmr.iova+length<=sizeof(mr->data));
    if(dir==RXE_TO_MR_OBJ)memcpy(mr->data+va-mr->ibmr.iova,addr,length);
    else memcpy(addr,mr->data+va-mr->ibmr.iova,length);
    return 0;
}
static void rxe_mr_copy_dma(struct rxe_mr *mr,u64 va,void *addr,unsigned length,enum rxe_mr_copy_dir dir){
    (void)rxe_mr_copy_xarray(mr,va,addr,length,dir);
}
static struct rxe_dev device;static struct rxe_pd pd;static struct rxe_qp qp;static struct rxe_mr region;
static struct rxe_mr *pending;
static int wait_calls,wait_budget,cleanup_calls,cleanup_with_refs,free_calls;
static bool force_timeout,write_during_wait;
static void record_cleanup(struct rxe_pool_elem *elem){++cleanup_calls;if(elem->ref_cnt.refs)++cleanup_with_refs;}
static int wait_for_completion_timeout(struct completion *p,int timeout){
    ++wait_calls;wait_budget=timeout;
    assert(device.mr_pool.xa.entry==NULL); // lookup visibility removed before wait
    assert(lookup_mr(&pd,IB_ACCESS_REMOTE_WRITE,region.rkey,RXE_LOOKUP_REMOTE)==NULL);
    if(force_timeout)return 0;
    assert(pending!=NULL);u8 value=0x42;
    assert(rxe_mr_copy(pending,pending->ibmr.iova,&value,1,RXE_TO_MR_OBJ)==0);
    write_during_wait=true;rxe_put(pending);pending=NULL;
    assert(completion_done(p));return 1;
}
static void record_free(struct rxe_mr *mr){assert(mr==&region);++free_calls;}
#define kfree_rcu_mightsleep(p) record_free(p)
static void setup(int type){
    memset(&device,0,sizeof(device));memset(&region,0,sizeof(region));
    pd.ibpd.device=&device;qp.ibqp.device=&device;qp.ibqp.pd=&pd.ibpd;
    device.mr_pool.cleanup=record_cleanup;device.mr_pool.num_elem.value=1;
    region.ibmr=(struct ib_mr){.device=&device,.pd=&pd.ibpd,.type=type,.iova=4096,.length=16};
    region.elem=(struct rxe_pool_elem){.pool=&device.mr_pool,.obj=&region,.ref_cnt={1},.index=7};
    device.mr_pool.xa.entry=&region.elem;region.lkey=region.rkey=(7u<<8)|0x35;
    region.access=RXE_ACCESS_REMOTE;region.state=RXE_MR_STATE_VALID;memset(region.data,0xcc,16);
    warnings=cleanup_errors=wait_calls=wait_budget=cleanup_calls=cleanup_with_refs=free_calls=0;
    force_timeout=write_during_wait=false;pending=NULL;
}
/* Firmware/WQE recording substitutes. No DMA retirement is modeled here. */
struct mlx5_core_dev {int state;};
struct mlx5_ib_pd {u32 pdn;};
struct mlx5_ib_dev {struct mlx5_core_dev *mdev;struct {struct mlx5_ib_pd *pd;} umrc;};
struct mlx5_ib_mr {struct mlx5_ib_dev *dev;struct {u32 key;} mmkey;};
struct mlx5r_umr_wqe {struct {u64 mkey_mask;u32 flags;} ctrl_seg;struct {u32 free,pd,qpn,mkey_7_0;} mkey_seg;};
enum {MLX5_DEVICE_STATE_INTERNAL_ERROR=1,MLX5_UMR_INLINE=8};
#define mr_to_mdev(p) ((p)->dev)
#define to_mpd(p) (p)
#define MLX5_SET(type,p,field,value) ((p)->field=(value))
static u64 get_umr_update_pd_mask(void){return 2;}
static u64 get_umr_disable_mr_mask(void){return 4;}
static u32 mlx5_mkey_variant(u32 key){return key&0xff;}
static int post_calls,post_result;static u32 posted_key;static bool posted_ignore_free;
static struct mlx5r_umr_wqe posted_wqe;
static int mlx5r_umr_post_send_wait(struct mlx5_ib_dev *dev,u32 key,struct mlx5r_umr_wqe *wqe,bool ignore_free){
    assert(dev->mdev->state!=MLX5_DEVICE_STATE_INTERNAL_ERROR);++post_calls;posted_key=key;posted_wqe=*wqe;posted_ignore_free=ignore_free;
    return post_result;
}
